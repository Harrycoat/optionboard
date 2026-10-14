"""Read-only public summary of verified SORA paper ledger; no token or private data returned."""
from http.server import BaseHTTPRequestHandler
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import os,json,base64
from datetime import datetime

REPO="Harrycoat/optionboard"
BRANCH="feature/sora-pullback-20-50-20261013"
PATH="data/sora-paper-ledger.json"

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  try:
   token=os.environ.get("SORA_LEDGER_GITHUB_TOKEN","")
   if not token:raise RuntimeError("STORAGE_NOT_CONFIGURED")
   req=Request("https://api.github.com/repos/"+REPO+"/contents/"+PATH+"?ref="+BRANCH,
      headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
               "User-Agent":"SORA-public-paper-summary","X-GitHub-Api-Version":"2022-11-28"})
   with urlopen(req,timeout=8) as res:doc=json.loads(res.read())
   ledger=json.loads(base64.b64decode(doc["content"]))
   assert ledger["account_id"]=="SORA-01" and isinstance(ledger["trades"],list)
   buys=[]
   for t in ledger["trades"]:
    if t.get("mode")!="PAPER" or t.get("side")!="BUY":continue
    ticker=t.get("ticker");price=t.get("fill_price");qty=t.get("quantity")
    if not isinstance(ticker,str) or not isinstance(qty,int) or not isinstance(price,(int,float)):raise ValueError("BAD_TRADE")
    buys.append({"ticker":ticker,"quantity":qty,"fill_price":price,"entry_stage":t.get("entry_stage"),
       "source":t.get("source"),"reason":t.get("reason"),"fill_time_utc":t.get("fill_time_utc")})
   spent=round(sum(t["quantity"]*t["fill_price"] for t in buys),2)
   cash=round(float(ledger["initial_cash"])-spent,2)
   if cash<0 or abs(cash-float(ledger.get("cash",cash)))>0.01:raise ValueError("BAD_CASH")
   out={"account_id":"SORA-01","mode":"PAPER","initial_cash":ledger["initial_cash"],
      "cash":cash,"trade_count":len(buys),"trades":buys,"price_status":"NO_LIVE_VALUATION"}
   self.respond(200,out)
  except Exception:
   self.respond(503,{"error":"LEDGER_TEMPORARILY_UNAVAILABLE"})
 def respond(self,status,body):
  data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(data)))
  self.end_headers();self.wfile.write(data)
