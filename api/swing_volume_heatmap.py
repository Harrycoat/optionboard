"""GEXOption Swing Volume Heatmap beta.
Daily timeframe stock-discovery engine using Schwab daily candles.
NOTE: Schwab daily candles expose total volume, not aggressor-side trade volume.
Therefore this beta uses candle-position weighted volume pressure and labels it
as an ESTIMATE. It must not be presented as true bid/ask Volume Delta.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import base64, json, os, time
import requests

TOKEN_URL="https://api.schwabapi.com/v1/oauth/token"
HISTORY_URL="https://api.schwabapi.com/marketdata/v1/pricehistory"

UNIVERSE={
"AI / Mega Tech":["NVDA","MSFT","GOOGL","AMZN","META","AAPL","ORCL"],
"AI Semiconductor":["NVDA","AMD","AVGO","ARM","INTC","MU"],
"AI Networking":["ANET","CRDO","MRVL","ALAB","LITE","CIEN"],
"Memory / Storage":["MU","SNDK","WDC","STX"],
"AI Server / Hardware":["DELL","SMCI","HPE","VRT","APH"],
"AI Cloud / Data Center":["NBIS","IREN","CIFR","CLSK","VRT","ETN","GEV"],
"Cybersecurity":["CRWD","PANW","FTNT","OKTA","ZS","NET"],
"Robotics / Automation":["TSLA","ISRG","ROK"],
"Nuclear / Power":["CEG","VST","CCJ","LEU","OKLO","SMR"],
"Space / Defense":["RKLB","PLTR","LMT","RTX"],
"Healthcare":["LLY","TMO","VRTX","REGN","AMGN"],
"Financial":["JPM","BAC","WFC","GS","MS","COIN"]
}

def out(h,status,payload):
    h.send_response(status); h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Cache-Control","no-store"); h.send_header("Access-Control-Allow-Origin","*"); h.end_headers()
    h.wfile.write(json.dumps(payload,ensure_ascii=False).encode())

def token():
    cid=os.environ.get("SCHWAB_CLIENT_ID","").strip(); sec=os.environ.get("SCHWAB_CLIENT_SECRET","").strip()
    ref=os.environ.get("SCHWAB_REFRESH_TOKEN","").strip()
    if not cid or not sec or not ref: raise RuntimeError("Schwab environment variables are missing.")
    basic=base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r=requests.post(TOKEN_URL,headers={"Authorization":f"Basic {basic}","Content-Type":"application/x-www-form-urlencoded"},
                    data={"grant_type":"refresh_token","refresh_token":ref},timeout=20)
    if not r.ok: raise RuntimeError(f"Schwab token refresh failed ({r.status_code})")
    return r.json()["access_token"]

def candles(sym,access):
    end=int(time.time()*1000); start=end-(120*86400000)
    r=requests.get(HISTORY_URL,headers={"Authorization":f"Bearer {access}","Accept":"application/json"},
      params={"symbol":sym,"periodType":"year","period":1,"frequencyType":"daily","frequency":1,
              "startDate":start,"endDate":end,"needExtendedHoursData":"false","needPreviousClose":"true"},timeout=20)
    if not r.ok: raise RuntimeError(f"{sym}: price history {r.status_code}")
    return r.json().get("candles") or []

def pressure(c):
    hi=float(c.get("high") or 0); lo=float(c.get("low") or 0); close=float(c.get("close") or 0); vol=float(c.get("volume") or 0)
    rng=max(hi-lo,1e-9)
    # Close Location Value [-1,+1] * total volume = estimated daily pressure.
    clv=((close-lo)-(hi-close))/rng
    return clv*vol

def analyze(sym,access):
    cs=candles(sym,access)
    if len(cs)<25: raise RuntimeError(f"{sym}: insufficient daily candles")
    recent=cs[-25:]; ps=[pressure(x) for x in recent]; vols=[float(x.get("volume") or 0) for x in recent]
    p0,p1,p2=ps[-1],ps[-2],ps[-3]
    avg20=sum(vols[-21:-1])/20 if sum(vols[-21:-1]) else 1
    vr=vols[-1]/avg20
    # normalized pressure makes unlike-sized stocks comparable
    norm=p0/max(vols[-1],1)
    prev=p1/max(vols[-2],1)
    slope=norm-prev
    delta_up=(norm>prev and prev>(p2/max(vols[-3],1)))
    # State is volume-pressure first; price % is display only.
    if norm<=-.35: state="RED"
    elif norm<-.08: state="YELLOW"
    elif norm<=.12: state="SKY"
    elif norm<.42: state="GOLD"
    else: state="GREEN"
    o=float(recent[-1].get("open") or 0); close=float(recent[-1].get("close") or 0)
    ch=((close/o)-1)*100 if o else 0
    score=(2 if state=="SKY" else 0)+(3 if delta_up else 0)+min(vr,2)
    return {"ticker":sym,"state":state,"delta_up":delta_up,"pressure":round(norm,4),
            "pressure_prev":round(prev,4),"pressure_slope":round(slope,4),
            "volume":int(vols[-1]),"volume_ratio":round(vr,2),"price":round(close,2),
            "day_change_pct":round(ch,2),"score":round(score,2)}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q=parse_qs(urlparse(self.path).query); only=(q.get("ticker",[""])[0] or "").upper().strip()
        try:
            access=token(); sectors={}; flat=[]
            groups={"Custom":[only]} if only else UNIVERSE
            for sector,syms in groups.items():
                rows=[]
                for sym in syms:
                    try: rows.append(analyze(sym,access))
                    except Exception as e: rows.append({"ticker":sym,"error":str(e)})
                sectors[sector]=rows; flat += [x for x in rows if not x.get("error")]
            turns=sorted([x for x in flat if x["state"] in ("SKY","GOLD") and x["delta_up"]],
                         key=lambda x:(x["state"]!="SKY",-x["pressure_slope"]))[:12]
            out(self,200,{"timeframe":"DAILY","source":"Schwab Trader API",
                "delta_method":"ESTIMATED daily volume pressure (CLV × volume); not true bid/ask trade delta",
                "sectors":sectors,"delta_turn":turns})
        except Exception as e: out(self,500,{"error":str(e),"source":"Schwab Trader API"})
