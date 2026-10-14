"""Public paper-holdings quotes for SORA 01. No trading or privileged data exposed."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime,timezone
import json,math,os,sys
sys.path.insert(0,os.path.dirname(__file__))
from schwab_quote import _fetch_quote

TICKERS=("NVDA","TSLA","AMZN","PLTR","AAPL","META","MSFT","COST")
class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data={}
  for ticker in TICKERS:
   try:
    quote=_fetch_quote(ticker)
    price=quote.get("last")
    if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:
     data[ticker]={"status":"UNAVAILABLE"};continue
    data[ticker]={"status":"OK","price":round(float(price),4),
       "source":"Schwab Trader API","realtime":quote.get("realtime") is True}
   except Exception:
    data[ticker]={"status":"UNAVAILABLE"}
  payload={"asof_fetch_utc":datetime.now(timezone.utc).isoformat(),
           "quotes":data,"note":"Snapshot fetch time, not necessarily exchange quote timestamp. See realtime flag; do not assume live quote."}
  body=json.dumps(payload,ensure_ascii=False).encode()
  self.send_response(200);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
