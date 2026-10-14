"""Live-data research scan for active SORA robots. GET only, zero orders.

Uses only previous completed daily Massive candles (15 minute feed policy).
WATCH is informational. No actual TOS Smooth is connected, so no ENTRY/fills.
"""
from http.server import BaseHTTPRequestHandler
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
import json,os,sys
sys.path.insert(0,os.path.dirname(__file__))
from swing_radar_chart import aggregates
from sora_ten_opportunity_engine import daily_features,decide,rank_watch

# Initial verified-by-configuration watch universe. Expanded membership verification
# required before any entry can be eligible. These are not claims of index inclusion.
WATCHLIST=("NVDA","TSLA","AMZN","AAPL")
ROBOTS=("SORA-01","SORA-02","SORA-03","SORA-04")
class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  def read(ticker):
   bars=aggregates(ticker,"1d")
   return ticker,daily_features(bars)
  features={};failures={}
  # Hard cap four providers to bound Vercel request latency.
  try:
   with ThreadPoolExecutor(max_workers=4) as pool:
    futures={pool.submit(read,t):t for t in WATCHLIST}
    for fut in as_completed(futures):
     ticker=futures[fut]
     try:
      name,feat=fut.result()
      features[name]=feat
     except Exception as exc:failures[ticker]=type(exc).__name__+":"+str(exc)[:75]
  except Exception:failures["market_data"]="UNAVAILABLE"
  evaluations=[]
  for rid in ROBOTS:
   for ticker,feat in features.items():
    # Discovery only: index constituent verification and TOS 30m feed must be
    # connected before any automatic ENTRY. Do not set verified_member=True.
    e=decide(rid,ticker,feat,verified_member=False)
    evaluations.append(e)
  output={"mode":"RESEARCH_SCAN_ONLY","checked_at_utc":datetime.now(timezone.utc).isoformat(),
          "source":"Massive adjusted OHLCV, completed prior-day candles only",
          "data_frequency":"DELAYED_DAILY","universe":list(WATCHLIST),
          "active_robots_scanned":list(ROBOTS),"quotes_loaded":sorted(features),
          "feed_errors":failures,"paper_orders_executed":0,
          "status":"OK" if features else "DATA_UNAVAILABLE",
          "candidates":rank_watch(evaluations,40),
          "technical_snapshots":{t:{"close":round(f["price"],4),
             "sma20":round(f["sma20"],4),"sma50":round(f["sma50"],4),
             "sma50_rising":f["sma50_rising"],"near20":f["near20"],
             "near50":f["near50"],"volume_ratio":round(f["volume_ratio"],3),
             "last_bar_utc":datetime.fromtimestamp(f["bar_timestamp"]/1000,timezone.utc).isoformat()}
             for t,f in features.items()},
          "missing_integrations":["current S&P500/Nasdaq100 constituent verification",
            "completed 30m order-flow confirmation","TOS Smooth verified feed",
            "separate robot paper-ledger transaction executor and cron"]}
  self.reply(200 if features else 503,output)
 def reply(self,status,obj):
  body=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
