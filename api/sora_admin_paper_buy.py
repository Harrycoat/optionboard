"""Authenticated paper-only admin CORE buys, atomic GitHub-backed ledger commit.
Configure SORA_ADMIN_USER, SORA_ADMIN_PASSWORD_SHA256, SORA_LEDGER_GITHUB_TOKEN.
No brokerage or real order path exists. Separate simulation ledger (not static baseline).
"""
from http.server import BaseHTTPRequestHandler
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from datetime import datetime,timezone
import os,json,base64,hashlib,hmac,re
from urllib.parse import urlparse,parse_qs
from api.sora_paper_ledger_replay import confirm_balance

REPO="Harrycoat/optionboard"
BRANCH="feature/sora-pullback-20-50-20261013"
PATH="data/sora-paper-ledger.json"
ACCOUNTS={f"SORA-{n:02d}" for n in range(1,11)}
def ledger_path(account):
    if account not in ACCOUNTS:raise ValueError("INVALID_ACCOUNT")
    return PATH if account=="SORA-01" else f"data/sora-paper-ledger-{account.lower()}.json"
CORE={"NVDA","AMZN","PLTR","TSLA","AAPL","META","MSFT","COST"}
def auth(h):
    user=os.getenv("SORA_ADMIN_USER","")
    pw_hash=os.getenv("SORA_ADMIN_PASSWORD_SHA256","")
    if not user or not re.fullmatch(r"[a-fA-F0-9]{64}",pw_hash):return False
    try:
        kind,encoded=h.get("Authorization","").split(" ",1)
        u,p=base64.b64decode(encoded,validate=True).decode().split(":",1)
        return kind.lower()=="basic" and hmac.compare_digest(u,user) and hmac.compare_digest(hashlib.sha256(p.encode()).hexdigest(),pw_hash.lower())
    except (ValueError,UnicodeDecodeError):return False

def github(method,path,payload=None):
    token=os.getenv("SORA_LEDGER_GITHUB_TOKEN","")
    if not token:raise RuntimeError("LEDGER_STORAGE_NOT_CONFIGURED")
    req=Request("https://api.github.com/repos/"+REPO+"/contents/"+path+("?ref="+BRANCH if method=="GET" else ""),
                data=None if payload is None else json.dumps(payload).encode(),
                headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"SORA-paper-ledger","Content-Type":"application/json"},method=method)
    with urlopen(req,timeout=9) as f:return json.loads(f.read())

def validate(o):
    if not isinstance(o,dict):raise ValueError("INVALID_BODY")
    if o.get("account_id") not in ACCOUNTS:raise ValueError("INVALID_ACCOUNT")
    ticker=o.get("ticker")
    if not isinstance(ticker,str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",ticker):raise ValueError("INVALID_SYMBOL")
    price=o.get("fill_price");qty=o.get("quantity")
    if not isinstance(price,(int,float)) or isinstance(price,bool) or not 0<price<1000000:raise ValueError("INVALID_PRICE")
    if not isinstance(qty,int) or isinstance(qty,bool) or not 0<qty<=100000:raise ValueError("INVALID_QUANTITY")
    stage=o.get("entry_stage")
    if stage not in ("20MA_FIRST","50MA_SECOND","MANUAL_OTHER"):raise ValueError("INVALID_STAGE")
    reason=o.get("reason")
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=300:raise ValueError("REASON_REQUIRED")
    key=o.get("request_id")
    if not isinstance(key,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}",key):raise ValueError("INVALID_REQUEST_ID")
    return {"account_id":o["account_id"],"ticker":ticker,"fill_price":round(price,4),"quantity":qty,"entry_stage":stage,"reason":reason.strip(),"request_id":key}

class handler(BaseHTTPRequestHandler):
    def respond(self,code,obj):
        data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(data)))
        self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not auth(self.headers):
            # JSON-only 401: fetch() must never trigger a browser-native Basic Auth prompt.
            self.respond(401,{"error":"ADMIN_AUTH_REQUIRED","message":"Check Preview variables and credentials. No browser popup is required."});return
        try:
            account=parse_qs(urlparse(self.path).query).get("account_id",["SORA-01"])[0]
            path=ledger_path(account)
            x=github("GET",path);ledger=json.loads(base64.b64decode(x["content"]))
            self.respond(200,ledger)
        except HTTPError as e:
            if e.code==404:self.respond(200,{"version":1,"account_id":account,"initial_cash":100000,"cash":100000,"trades":[]} )
            else:self.respond(503,{"error":"LEDGER_READ_FAILED"})
        except Exception:self.respond(503,{"error":"LEDGER_UNAVAILABLE"})
    def do_POST(self):
        if not auth(self.headers):self.respond(401,{"error":"ADMIN_AUTH_REQUIRED"});return
        if self.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":self.respond(415,{"error":"JSON_ONLY"});return
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<=0 or n>4096:raise ValueError("INVALID_LENGTH")
            v=validate(json.loads(self.rfile.read(n)))
        except (ValueError,TypeError,json.JSONDecodeError) as e:self.respond(400,{"error":str(e)});return
        try:
            account=v["account_id"]
            path=ledger_path(account)
            try:
                obj=github("GET",path);sha=obj["sha"];ledger=json.loads(base64.b64decode(obj["content"]))
            except HTTPError as e:
                if e.code!=404:raise
                sha=None;ledger={"version":1,"account_id":account,"initial_cash":100000,"cash":100000,"trades":[]}
            if ledger.get("account_id")!=account or not isinstance(ledger.get("trades"),list):raise ValueError("CORRUPT_LEDGER")
            state=confirm_balance(ledger)
            trades=ledger["trades"]
            existing=next((t for t in trades if t.get("request_id")==v["request_id"]),None)
            if existing:
                self.respond(200,{"status":"ALREADY_SAVED","trade":existing});return
            available=state["cash"]
            total=round(v["fill_price"]*v["quantity"],2)
            if total>available+0.00001:self.respond(409,{"error":"INSUFFICIENT_PAPER_CASH","available":available});return
            count=sum(1 for t in trades if t.get("ticker")==v["ticker"])
            if v["entry_stage"]=="20MA_FIRST" and any(t.get("ticker")==v["ticker"] and t.get("entry_stage")=="20MA_FIRST" for t in trades):
                self.respond(409,{"error":"FIRST_STAGE_ALREADY_BOUGHT"});return
            if v["entry_stage"]=="50MA_SECOND" and any(t.get("ticker")==v["ticker"] and t.get("entry_stage")=="50MA_SECOND" for t in trades):
                self.respond(409,{"error":"SECOND_STAGE_ALREADY_BOUGHT"});return
            t={**v,"side":"BUY","bucket":"CORE","source":"ADMIN","mode":"PAPER","fill_time_utc":datetime.now(timezone.utc).isoformat(),"sequence":len(trades)+1}
            ledger["trades"].append(t)
            ledger["cash"]=round(available-total,2)
            encoded=base64.b64encode((json.dumps(ledger,ensure_ascii=False,indent=2)+"\n").encode()).decode()
            body={"message":"data: record administrator CORE paper buy "+v["ticker"],"content":encoded,"branch":BRANCH}
            if sha:body["sha"]=sha
            github("PUT",path,body)
            self.respond(201,{"status":"SAVED","trade":t,"cash":ledger["cash"]})
        except HTTPError as e:
            self.respond(409 if e.code in (409,422) else 503,{"error":"SAVE_CONFLICT_RETRY" if e.code in (409,422) else "LEDGER_WRITE_FAILED"})
        except (URLError,ValueError,RuntimeError):self.respond(503,{"error":"LEDGER_UNAVAILABLE"})
