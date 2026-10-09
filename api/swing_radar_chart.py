"""GEXOption mobile swing chart beta: delayed candles, Hull20, estimated volume pressure and optional walls."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import MASSIVE_API_BASE, _massive_get, analyze_ticker

ET = ZoneInfo("America/New_York")
VALID_TF = {"5m": 5, "10m": 10, "30m": 30, "1h": 60, "4h": 240, "1d": None}

def wma(vals):
    n=len(vals)
    return sum((i+1)*v for i,v in enumerate(vals))/(n*(n+1)/2)

def hull20(closes):
    n=20
    raw=[]
    result=[]
    for idx in range(len(closes)):
        window=closes[:idx+1]
        raw.append(2*wma(window[-10:])-wma(window[-20:]) if len(window)>=20 else None)
        available=[v for v in raw if v is not None]
        result.append(round(wma(available[-4:]),5) if len(available)>=4 else None)
    return result

def aggregates(symbol,tf):
    now=datetime.now(timezone.utc)
    if tf=="1d":
        start=(now-timedelta(days=460)).date()
        unit,span="day",1
    else:
        start=(now-timedelta(days=18)).date()
        unit,span="minute",5
    data=_massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{symbol}/range/{span}/{unit}/{start}/{now.date()}",
        {"adjusted":"true","sort":"asc","limit":50000})
    rows=[]
    for b in data.get("results") or []:
        try:
            ts=int(b["t"])
            dt=datetime.fromtimestamp(ts/1000,timezone.utc).astimezone(ET)
            if tf!="1d" and not ((dt.hour==9 and dt.minute>=30) or (10<=dt.hour<16)):
                continue
            if tf!="1d" and (dt.hour>=16 or ts+300000>now.timestamp()*1000):
                continue
            rows.append({"t":ts,"o":float(b["o"]),"h":float(b["h"]),
                "l":float(b["l"]),"c":float(b["c"]),"v":float(b.get("v") or 0)})
        except (KeyError,ValueError,TypeError):
            continue
    if tf=="1d":return rows[-150:]
    duration=VALID_TF[tf]
    if duration==5:return rows[-180:]
    # Aggregate bars by regular-session buckets; 4h uses 09:30-13:30 and 13:30-16:00 ET.
    grouped={}
    for b in rows:
        dt=datetime.fromtimestamp(b["t"]/1000,timezone.utc).astimezone(ET)
        offset=(dt.hour*60+dt.minute)-(9*60+30)
        bucket=offset//duration
        key=(dt.date().isoformat(),bucket)
        grouped.setdefault(key,[]).append(b)
    out=[]
    for batch in grouped.values():
        # Exclude unfinished resampled candles, not only unfinished base 5-minute bars.
        if len(batch)<(duration//5) and len(batch)!=(390-duration*( (390-1)//duration))//5:
            continue
        out.append({"t":batch[0]["t"],"o":batch[0]["o"],
            "h":max(b["h"] for b in batch),"l":min(b["l"] for b in batch),
            "c":batch[-1]["c"],"v":sum(b["v"] for b in batch)})
    return out[-180:]

def indicators(rows):
    closes=[x["c"] for x in rows]
    hull=hull20(closes)
    pressure=[]
    for b in rows:
        spread=b["h"]-b["l"]
        pressure.append(b["v"]*(2*b["c"]-b["h"]-b["l"])/spread if spread>0 else 0)
    volbase=max(1, sum(x["v"] for x in rows[-21:-1])/max(1,len(rows[-21:-1])))
    a=2/(5+1)
    smooth=[]
    val=0.0
    for i,p in enumerate(pressure):
        norm=p/volbase
        val=norm if i==0 else a*norm+(1-a)*val
        smooth.append(round(val,4))
    return [{"t":b["t"],"open":b["o"],"high":b["h"],"low":b["l"],
            "close":b["c"],"volume":round(b["v"]),"hull20":hull[i],
            "evp":smooth[i]} for i,b in enumerate(rows)]

def get_walls(symbol):
    try:
        obj=analyze_ticker(symbol,skip_stage=True)
        return {"put_wall":obj.get("put_wall"),"call_wall":obj.get("call_wall"),
                "gamma_flip":obj.get("gamma_flip"),"asof_utc":obj.get("generated_at"),
                "expiry":obj.get("expiry_used"),"source":"GEXOption Massive options analysis",
                "stale_underlying":obj.get("is_stale_price")}
    except Exception:
        return {"put_wall":None,"call_wall":None,"gamma_flip":None,
                "status":"UNAVAILABLE","message":"Options wall data unavailable; no estimates inserted."}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        args=parse_qs(urlparse(self.path).query)
        symbol=(args.get("ticker") or [""])[0].strip().upper()
        tf=(args.get("tf") or ["1d"])[0].lower()
        walls=(args.get("walls") or ["0"])[0]=="1"
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",symbol) or tf not in VALID_TF:
            status,body=400,{"error":"Invalid ticker or timeframe"}
        else:
            try:
                rows=aggregates(symbol,tf)
                if len(rows)<25: raise ValueError("Not enough completed candles to calculate Hull20")
                data=indicators(rows)
                status=200
                body={"ticker":symbol,"tf":tf,"bars":data,"source":"Massive delayed OHLCV",
                    "last_bar_utc":datetime.fromtimestamp(data[-1]["t"]/1000,timezone.utc).isoformat(),
                    "generated_at_utc":datetime.now(timezone.utc).isoformat(),
                    "wall":get_walls(symbol) if walls else {"status":"NOT_REQUESTED"},
                    "notice":"EVP is estimated, not actual TOS Smooth. Entry decisions require real-time TOS confirmation."}
            except Exception:
                status,body=503,{"error":"Price data unavailable or incomplete"}
        payload=json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","private, max-age=60")
        self.end_headers()
        self.wfile.write(payload)
