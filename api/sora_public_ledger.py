"""Read-only public summary of verified SORA paper ledger; no token or private data returned."""
from http.server import BaseHTTPRequestHandler
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import os,json,base64,sys,re
from urllib.parse import parse_qs,urlparse
sys.path.insert(0,os.path.dirname(__file__))
from sora_paper_ledger_replay import confirm_balance
from datetime import datetime

REPO="Harrycoat/optionboard"
BRANCH="feature/sora-pullback-20-50-20261013"
PATH="data/sora-paper-ledger.json"

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  try:
   token=os.environ.get("SORA_LEDGER_GITHUB_TOKEN","")
   if not token:raise RuntimeError("STORAGE_NOT_CONFIGURED")
   account=parse_qs(urlparse(self.path).query).get("account_id",["SORA-01"])[0]
   if not re.fullmatch(r"SORA-(0[1-9]|10)",account):return self.respond(400,{"error":"INVALID_ACCOUNT"})
   path=PATH if account=="SORA-01" else "data/sora-paper-ledger-"+account.lower()+".json"
   req=Request("https://api.github.com/repos/"+REPO+"/contents/"+path+"?ref="+BRANCH,
      headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
               "User-Agent":"SORA-public-paper-summary","X-GitHub-Api-Version":"2022-11-28"})
   with urlopen(req,timeout=8) as res:doc=json.loads(res.read())
   ledger=json.loads(base64.b64decode(doc["content"]))
   assert ledger["account_id"]==account and isinstance(ledger["trades"],list)
   state=confirm_balance(ledger)
   # Public presentation contains no credentials or request IDs.
   events=[]
   for t in ledger["trades"]:
    if t.get("mode")!="PAPER":continue
    events.append({"ticker":t.get("ticker"),"quantity":t.get("quantity"),
      "fill_price":t.get("fill_price"),"entry_stage":t.get("entry_stage"),
      "exit_stage":t.get("exit_stage"),"side":t.get("side"),
      "source":t.get("source"),"reason":t.get("reason"),
      "fill_time_utc":t.get("fill_time_utc")})
   out={"account_id":account,"mode":"PAPER","initial_cash":ledger["initial_cash"],
      "cash":state["cash"],"trade_count":len(events),"trades":events,
      "positions":state["positions"],"realized_pnl":state["realized_pnl"],
      "price_status":"NO_LIVE_VALUATION"}
   self.respond(200,out)
  except HTTPError as err:
   if err.code==404:self.respond(200,{"account_id":account,"mode":"PAPER","initial_cash":100000,"cash":100000,"trade_count":0,"trades":[],"positions":[],"realized_pnl":0,"price_status":"NO_LIVE_VALUATION"})
   else:self.respond(503,{"error":"LEDGER_TEMPORARILY_UNAVAILABLE"})
  except Exception:
   self.respond(503,{"error":"LEDGER_TEMPORARILY_UNAVAILABLE"})
 def respond(self,status,body):
  data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(data)))
  self.end_headers();self.wfile.write(data)
