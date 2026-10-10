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

def session_bounds(day):
    import exchange_calendars as xc
    cal=xc.get_calendar("XNYS")
    if not cal.is_session(str(day)):return None
    return int(cal.session_open(str(day)).timestamp()*1000),int(cal.session_close(str(day)).timestamp()*1000)

def expected_session(cutoff_ms):
    day=datetime.fromtimestamp(cutoff_ms/1000,timezone.utc).astimezone(ET).date()
    for _ in range(10):
        bounds=session_bounds(day)
        if bounds and bounds[0]<cutoff_ms:return day,bounds
        day-=timedelta(days=1)
    raise ValueError("CALENDAR_UNAVAILABLE")

def resample(rows, duration, cutoff_ms):
    grouped={}
    for b in sorted(rows,key=lambda r:r['t']):
        dt=datetime.fromtimestamp(b['t']/1000,timezone.utc).astimezone(ET)
        bounds=session_bounds(dt.date())
        if not bounds:continue
        op,cl=bounds
        if not op<=b['t']<cl:continue
        start=op+((b['t']-op)//(duration*60000))*duration*60000
        end=min(start+duration*60000,cl)
        grouped.setdefault((start,end),{})[b['t']]=b
    result=[]
    for (start,end),bytime in sorted(grouped.items()):
        batch=list(bytime.values())
        expected=set(range(start,end,300000))
        complete=end<=cutoff_ms
        coverage=set(bytime)==expected
        result.append({'t':start,'end_ms':end,'o':batch[0]['o'],'h':max(b['h'] for b in batch),
            'l':min(b['l'] for b in batch),'c':batch[-1]['c'],'v':sum(b['v'] for b in batch),
            'complete':complete,'coverage_ok':coverage,'expected_base_bars':len(expected),'actual_base_bars':len(batch)})
    return result

def aggregates(symbol,tf,include_partial=False):
    now=datetime.now(timezone.utc);cutoff=int((now-timedelta(minutes=15)).timestamp()*1000)
    start=(now-timedelta(days=460 if tf=='1d' else 30)).date()
    unit,span=('day',1) if tf=='1d' else ('minute',5)
    # Isolated radar requests have bounded network time; existing engine is unchanged.
    import requests
    from options_engine import MASSIVE_API_KEY
    if not MASSIVE_API_KEY:raise ValueError('DATA_NOT_CONFIGURED: MASSIVE_API_KEY')
    try:
        response=requests.get(f"{MASSIVE_API_BASE}/v2/aggs/ticker/{symbol}/range/{span}/{unit}/{start}/{now.date()}",
        params={'adjusted':'true','sort':'asc','limit':50000,'apiKey':MASSIVE_API_KEY},timeout=(2,8))
    except requests.RequestException:raise ValueError('DATA_NETWORK_ERROR') from None
    if response.status_code!=200:raise ValueError('DATA_PROVIDER_HTTP_'+str(response.status_code))
    try:data=response.json()
    except ValueError:raise ValueError('DATA_INVALID_JSON') from None
    if data.get('status') not in ('OK','DELAYED'):raise ValueError('DATA_PROVIDER_ERROR')
    if data.get('next_url'):raise ValueError('DATA_TRUNCATED: pagination required')
    rows=[]
    for b in data.get('results') or []:
        row={k:float(b[k]) for k in ('o','h','l','c','v')};row['t']=int(b['t'])
        if row['l']>row['h'] or row['c']<=0:raise ValueError('DATA_INVALID')
        rows.append(row)
    if tf=='1d':
        out=[]
        for row in rows:
            day=datetime.fromtimestamp(row['t']/1000,timezone.utc).astimezone(ET).date()
            bounds=session_bounds(day)
            if bounds and bounds[1]<=cutoff:out.append(dict(row,end_ms=bounds[1],complete=True,coverage_ok=True))
        expected,bounds=expected_session(cutoff)
        if bounds[1]>cutoff:expected,_=expected_session(bounds[0]-1)
        if not out or datetime.fromtimestamp(out[-1]["t"]/1000,timezone.utc).astimezone(ET).date()!=expected:raise ValueError("DATA_STALE: daily session missing")
        return out[-300:]
    out=resample(rows,VALID_TF[tf],cutoff)
    completed=[b for b in out if b['complete']]
    day,bounds=expected_session(cutoff)
    expected_end=bounds[0]+((min(cutoff,bounds[1])-bounds[0])//(VALID_TF[tf]*60000))*VALID_TF[tf]*60000
    if cutoff>=bounds[1]:expected_end=bounds[1]
    if expected_end>bounds[0] and (not completed or completed[-1]['end_ms']<expected_end):raise ValueError('DATA_STALE: latest completed candle missing')
    # Missing eligible bars can be illiquidity or an outage: never silently call it no signal.
    if any(not b['coverage_ok'] for b in completed[-60:]):raise ValueError('DATA_GAP: incomplete 5m coverage in recent candles')
    return (out if include_partial else completed)[-220:]

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
        base=max(1,sum(b["v"] for b in rows[max(0,i-20):i])/max(1,min(i,20)))
        norm=p/base
        val=norm if i==0 else a*norm+(1-a)*val
        smooth.append(round(val,4))
    return [{"t":b["t"],"open":b["o"],"high":b["h"],"low":b["l"],
            "close":b["c"],"volume":round(b["v"]),"hull20":hull[i],
            "evp":smooth[i],"complete":b.get("complete",True),"coverage_ok":b.get("coverage_ok",True)} for i,b in enumerate(rows)]

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
        from swing_access import require_access
        if not require_access(self):return
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
