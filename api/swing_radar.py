"""GEXOption EARLY WATCH beta. Delayed Massive OHLCV; NOT actual trade delta."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import math
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import _massive_get, MASSIVE_API_BASE

ET = ZoneInfo("America/New_York")
# Small, explicitly versioned starting universe; NOT comprehensive sector leadership.
SECTORS = {
 "XLK": ("Technology", ["MSFT", "AAPL", "NVDA"]),
 "XLF": ("Financials", ["JPM", "GS", "BAC"]),
 "XLV": ("Healthcare", ["LLY", "ISRG", "VRTX"]),
 "XLY": ("Consumer Discretionary", ["AMZN", "TSLA", "HD"]),
 "XLP": ("Consumer Staples", ["COST", "WMT", "PG"]),
 "XLE": ("Energy", ["XOM", "CVX", "COP"]),
 "XLI": ("Industrials", ["GE", "CAT", "ETN"]),
 "XLB": ("Materials", ["LIN", "FCX", "NEM"]),
 "XLU": ("Utilities", ["NEE", "CEG", "VST"]),
 "XLRE": ("Real Estate", ["PLD", "AMT", "EQIX"]),
 "XLC": ("Communication", ["META", "GOOGL", "NFLX"]),
 "SMH": ("AI Semiconductors", ["NVDA", "AVGO", "AMD"]),
 "DRAM": ("Memory / Storage", ["MU", "SNDK", "WDC"]),
 "PAVE": ("Power / Infrastructure", ["VRT", "ETN", "GEV"]),
}
UNIVERSE_VERSION = "beta-1"

def daily(symbol):
    now = date.today()
    start = now - timedelta(days=460)
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{now}",
        {"adjusted": "true", "sort": "asc", "limit": 500}
    )
    rows = []
    for r in data.get("results") or []:
        try:
            if float(r["c"]) <= 0: continue
            rows.append({"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]),
                         "l": float(r["l"]), "c": float(r["c"]), "v": float(r.get("v") or 0)})
        except (ValueError, TypeError, KeyError): continue
    if len(rows) < 220: raise ValueError(f"{symbol}: insufficient daily history ({len(rows)})")
    return rows

def recent_intraday(symbol):
    today = datetime.now(ET).date()
    start = today - timedelta(days=3)
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{symbol}/range/5/minute/{start}/{today}",
        {"adjusted": "true", "sort": "asc", "limit": 2500}
    )
    result = []
    for r in data.get("results") or []:
        try:
            ts = int(r["t"])
            loc = datetime.fromtimestamp(ts/1000, timezone.utc).astimezone(ET)
            if loc.date() != today or not ((loc.hour == 9 and loc.minute >= 30) or (10 <= loc.hour < 16)):
                continue
            # Only completed 5-minute buckets. Provider may be delayed.
            if ts + 300000 > datetime.now(timezone.utc).timestamp()*1000: continue
            result.append({"t": ts, "o":float(r["o"]), "h":float(r["h"]),
                           "l":float(r["l"]), "c":float(r["c"]), "v":float(r.get("v") or 0)})
        except (ValueError, TypeError, KeyError): continue
    return result

def bars_with_partial(symbol):
    bars = daily(symbol)
    incoming = recent_intraday(symbol)
    latest = datetime.fromtimestamp(bars[-1]["t"]/1000, timezone.utc).astimezone(ET).date()
    today = datetime.now(ET).date()
    # Remove provider's potentially unfinished daily candle on market days.
    if latest == today: bars = bars[:-1]
    partial = False
    asof = bars[-1]["t"]
    if incoming:
        part = {"t":incoming[-1]["t"],"o":incoming[0]["o"],
                "h":max(r["h"] for r in incoming),"l":min(r["l"] for r in incoming),
                "c":incoming[-1]["c"],"v":sum(r["v"] for r in incoming)}
        bars.append(part)
        partial = True
        asof = incoming[-1]["t"]+300000
    return bars, partial, asof

def ema(seq, n):
    a = 2/(n+1)
    value = seq[0]
    out=[]
    for x in seq:
        value=a*x+(1-a)*value
        out.append(value)
    return out

def score_stock(symbol, bars, partial, asof):
    c=[b["c"] for b in bars]
    if len(c)<220: raise ValueError("missing history")
    sma20=[sum(c[i-19:i+1])/20 for i in range(19,len(c))]
    sma50=[sum(c[i-49:i+1])/50 for i in range(49,len(c))]
    sma200=sum(c[-200:])/200
    ma=sma20[-1]; prevma=sma20[-2]; last=bars[-1]
    touch=any(b["l"]<=sum(c[i-19:i+1])/20<=b["h"]
              for i,b in list(enumerate(bars))[-5:] if i>=19)
    near=abs(last["c"]/ma-1)*100<=2
    trend=sma50[-1]>sma50[-6] and last["c"]>sma200 and sma50[-1]>sma200
    # EVP: a bar-location weighted VOLUME proxy. Not aggressor-side delta.
    pressure=[b["v"]*(2*b["c"]-b["h"]-b["l"])/(b["h"]-b["l"]) if b["h"]>b["l"] else 0 for b in bars]
    base=max(1,sum(b["v"] for b in bars[-21:-1])/20)
    sm=ema([v/base for v in pressure],5)
    # We seek still-negative or near-zero pressure with improving slope, not breakout.
    weakening=sm[-1]>sm[-2] and sm[-1]<=0.30
    # Compression compares 3-bar range versus previous 5-bar range, normalized by close.
    recent_rng=(max(b["h"] for b in bars[-3:])-min(b["l"] for b in bars[-3:]))/last["c"]
    prior_rng=(max(b["h"] for b in bars[-8:-3])-min(b["l"] for b in bars[-8:-3]))/bars[-4]["c"]
    compress=recent_rng < prior_rng*.90 and min(b["l"] for b in bars[-3:]) >= min(b["l"] for b in bars[-8:-3])*.99
    breakout=last["c"]>max(b["h"] for b in bars[-5:-1])*1.005
    invalid=last["c"]<min(b["l"] for b in bars[-6:-1])*.99
    falling_volume=bars[-1]["v"]<bars[-2]["v"] if not partial else None # partial day incomparable
    watch=trend and (touch or near) and weakening and compress and not breakout and not invalid
    stop=min(b["l"] for b in bars[-5:])*.995
    risk=(last["c"]-stop)/last["c"]*100
    if risk>9: watch=False
    points=(20 if trend else 0)+(20 if touch or near else 0)+(20 if weakening else 0)+(15 if compress else 0)+(5 if not breakout else 0)
    return {"symbol":symbol,"price":round(last["c"],2),"sma20":round(ma,2),
            "sma20_distance_pct":round((last["c"]/ma-1)*100,2),
            "touched_sma20":touch,"trend_ok":trend,"evp_smooth":round(sm[-1],3),
            "evp_improving":weakening,"compression":compress,"breakout":breakout,
            "risk_pct":round(risk,2),"stop_idea":round(stop,2),
            "watch":bool(watch),"score":points,"partial_daily":partial,
            "volume_comparison":"not evaluated on partial daily bars",
            "asof_utc":datetime.fromtimestamp(asof/1000, timezone.utc).isoformat()}

def rank_etf(bars, spy):
    c=[b["c"] for b in bars]; s=[b["c"] for b in spy]
    # Align by exchange session DATE, not by last N bars.
    def idx(rows):
        return {datetime.fromtimestamp(b["t"]/1000,timezone.utc).astimezone(ET).date():b["c"] for b in rows}
    ei,si=idx(bars),idx(spy); days=sorted(set(ei)&set(si))
    if len(days)<25: raise ValueError("ETF/SPY have insufficient aligned sessions")
    v=[ei[d]/si[d] for d in days]
    def rel(n):return (v[-1]/v[-1-n]-1)*100
    return {"relative5_pct":round(rel(5),2),"relative20_pct":round(rel(20),2),
            "improving":v[-1]>v[-4], "asof_date":str(days[-1])}

def process(sector):
    name,symbols=SECTORS[sector]
    spy,spy_partial,spy_asof=bars_with_partial("SPY")
    etf,ep,et=bars_with_partial(sector)
    rotation=rank_etf(etf,spy)
    rows=[]; errors=[]
    for symbol in symbols:
        try:
            bars,p,t=bars_with_partial(symbol)
            rows.append(score_stock(symbol,bars,p,t))
        except Exception as exc:
            errors.append({"symbol":symbol,"reason":str(exc)[:120]})
    return {"sector":sector,"name":name,"universe_version":UNIVERSE_VERSION,
            "rotation":rotation,"candidates":sorted(rows,key=lambda x:(x["watch"],x["score"]),reverse=True),
            "errors":errors,"source":"Massive delayed OHLCV",
            "warning":"EVP estimates candle pressure, NOT TOS OrderFlow or true bid/ask delta.",
            "partial_daily":ep,"asof_utc":datetime.fromtimestamp(min(spy_asof,et)/1000,timezone.utc).isoformat()}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        args=parse_qs(urlparse(self.path).query)
        sector=(args.get("sector") or [""])[0].upper()
        try:
            if sector not in SECTORS: raise ValueError("Unknown sector; use one of: "+",".join(SECTORS))
            body=process(sector); status=200
        except ValueError as exc: body={"error":str(exc)}; status=400
        except Exception as exc: body={"error":"Market data unavailable","detail":str(exc)[:160]}; status=503
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","private, max-age=60")
        self.end_headers()
        self.wfile.write(data)
