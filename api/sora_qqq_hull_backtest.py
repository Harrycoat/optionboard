"""QQQ 2m HMA21 line-cross research backtest, no orders.

PROXY: a single Hull Moving Average line, not the user's exact TOS Hull21 band.
Counts close-to-close transitions only. Intrabar flips require tick/second data.
"""
from http.server import BaseHTTPRequestHandler
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import os,sys,math,json
from urllib.parse import urlparse,parse_qs
import requests
sys.path.insert(0,os.path.dirname(__file__))
from options_engine import MASSIVE_API_BASE,MASSIVE_API_KEY
ET=ZoneInfo("America/New_York")

def wma(x,n):
 if len(x)<n:return None
 seg=x[-n:];return sum((i+1)*v for i,v in enumerate(seg))/(n*(n+1)/2)

def hma21(prices):
 # HMA(n) = WMA(2*WMA(price,n/2)-WMA(price,n),sqrt(n)).
 # n/2=10, sqrt(21)=5 by rounding convention. TOS plot may differ.
 raw=[];out=[]
 for i in range(len(prices)):
  a=wma(prices[:i+1],10);b=wma(prices[:i+1],21)
  raw.append(2*a-b if a is not None and b is not None else None)
  good=raw[max(0,i-4):i+1]
  out.append(wma(good,5) if len(good)==5 and all(v is not None for v in good) else None)
 return out

def tally(rows):
 daily={};total_buys=0;total_sells=0
 days={}
 for row in rows:
  dt=datetime.fromtimestamp(row["t"]/1000,timezone.utc).astimezone(ET)
  if dt.weekday()>=5 or not (dt.hour>9 or(dt.hour==9 and dt.minute>=30)) or dt.hour>=16:continue
  days.setdefault(dt.date().isoformat(),[]).append(row)
 for day,bars in sorted(days.items()):
  bars.sort(key=lambda x:x["t"])
  if len(bars)<26:continue
  closes=[r["c"] for r in bars];h=hma21(closes);buys=0;sells=0
  for i in range(1,len(bars)):
   if h[i] is None or h[i-1] is None:continue
   # Approximation: close crosses HMA21 between two completed 2m bars.
   if closes[i-1]<=h[i-1] and closes[i]>h[i]:buys+=1
   if closes[i-1]>=h[i-1] and closes[i]<h[i]:sells+=1
  daily[day]={"bars":len(bars),"up_crosses":buys,"down_crosses":sells}
  total_buys+=buys;total_sells+=sells
 return {"days":daily,"up_crosses":total_buys,"down_crosses":total_sells,
         "roundtrips_not_inferred":True}

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  q=parse_qs(urlparse(self.path).query)
  try:days=max(1,min(10,int(q.get("days",["5"])[0])))
  except (ValueError,TypeError):days=5
  if not MASSIVE_API_KEY:
   self.reply(503,{"status":"DATA_NOT_CONFIGURED"});return
  now=datetime.now(timezone.utc)
  start=(now-timedelta(days=days+5)).date()
  try:
   res=requests.get(f"{MASSIVE_API_BASE}/v2/aggs/ticker/QQQ/range/2/minute/{start}/{now.date()}",
      params={"adjusted":"true","sort":"asc","limit":50000,"apiKey":MASSIVE_API_KEY},timeout=(3,12))
   res.raise_for_status();raw=res.json()
   if raw.get("next_url"):raise ValueError("PAGINATION_REQUIRED")
   rows=[dict(t=int(b["t"]),c=float(b["c"])) for b in raw.get("results",[])]
   # discard any incomplete 2m bar, plus delayed-feed buffer
   cutoff=(now-timedelta(minutes=15)).timestamp()*1000
   rows=[r for r in rows if r["t"]+120000<=cutoff]
   results=tally(rows)
   # Only last requested RTH sessions
   subset=dict(list(results["days"].items())[-days:])
   out={"symbol":"QQQ","interval":"2m","basis":"COMPLETED_BAR_CLOSES",
      "indicator":"HMA21_SINGLE_LINE_PROXY_NOT_TOS_HULL_BAND",
      "model":"n/2=10, sqrt(n)=5 rounded WMA","asof_utc":now.isoformat(),
      "days":subset,"day_count":len(subset),
      "up_crosses":sum(v["up_crosses"] for v in subset.values()),
      "down_crosses":sum(v["down_crosses"] for v in subset.values()),
      "limitations":["Intrabar 1-second flips are not countable from 2m candles.",
       "Not the exact TOS Hull21 band. Band definition is required.",
       "Crosses are not net profitable trades or matched round trips.",
       "2-minute historical availability depends on Massive entitlement."]}
   self.reply(200 if subset else 503,out)
  except Exception as e:
   self.reply(503,{"status":"DATA_UNAVAILABLE","reason":type(e).__name__,
    "note":"No counts produced without verified 2m market bars."})
 def reply(self,status,obj):
  b=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(b)))
  self.end_headers();self.wfile.write(b)
