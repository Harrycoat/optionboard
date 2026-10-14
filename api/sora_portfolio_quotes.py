"""Public paper-holdings quotes for SORA 01. No trading or privileged data exposed."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime,timezone
import json,math,os,sys,requests
sys.path.insert(0,os.path.dirname(__file__))
from schwab_quote import _refresh_access_token,QUOTES_URL

TICKERS=("NVDA","TSLA","AMZN","PLTR","AAPL","META","MSFT","COST")
class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data={t:{"status":"UNAVAILABLE"} for t in TICKERS}
  try:
   token=_refresh_access_token()
   response=requests.get(QUOTES_URL,headers={"Authorization":"Bearer "+token,"Accept":"application/json"},
      params={"symbols":",".join(TICKERS),"fields":"quote,reference","indicative":"false"},timeout=(3,10))
   response.raise_for_status()
   batch=response.json()
   for ticker in TICKERS:
    row=batch.get(ticker) or {}
    quote=row.get("quote") or {}
    price=quote.get("lastPrice")
    if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:continue
    data[ticker]={"status":"OK","price":round(float(price),4),"source":"Schwab Trader API",
      "realtime":row.get("realtime") is True}
  except Exception:pass
  payload={"asof_fetch_utc":datetime.now(timezone.utc).isoformat(),
           "quotes":data,"note":"Snapshot fetch time, not necessarily exchange quote timestamp. See realtime flag; do not assume live quote."}
  body=json.dumps(payload,ensure_ascii=False).encode()
  self.send_response(200);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
