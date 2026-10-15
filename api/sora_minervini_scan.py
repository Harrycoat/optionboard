"""Read-only Minervini-inspired SEPA/VCP candidate scan for SORA 06.

Heuristic VCP candidate, not an assertion of authentic discretionary VCP.
Completed daily Massive bars only. No transactions, no TOS Smooth proxy.
"""
from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json,os,sys,math
sys.path.insert(0,os.path.dirname(__file__))
from swing_radar_chart import aggregates

UNIVERSE=("NVDA","AVGO","AMD","MU","AMZN","META","MSFT","GOOGL","PLTR","TSLA","AAPL","COST","NFLX","VRT","GEV","ETN")
def analyze(rows):
 if len(rows)<205 or not all(b.get("complete") is True for b in rows[-205:]):
  raise ValueError("INSUFFICIENT_COMPLETED_HISTORY")
 c=[float(b["c"]) for b in rows];v=[float(b["v"]) for b in rows]
 ma=lambda n:sum(c[-n:])/n
 m50,m150,m200=ma(50),ma(150),ma(200)
 prior200=sum(c[-205:-5])/200
 price=c[-1]
 trend=price>m50>m150>m200 and m200>prior200
 high52=max(float(b["h"]) for b in rows[-min(252,len(rows)):])
 low52=min(float(b["l"]) for b in rows[-min(252,len(rows)):])
 within_high=price>=high52*.75
 above_low=price>=low52*1.30
 # Three nonoverlapping contraction windows (oldest -> newest).
 windows=(rows[-60:-40],rows[-40:-20],rows[-20:])
 ranges=[(max(float(x["h"]) for x in w)-min(float(x["l"]) for x in w))/max(float(x["h"]) for x in w) for w in windows]
 vols=[sum(float(x["v"]) for x in w)/len(w) for w in windows]
 tightening=ranges[0]>ranges[1]>ranges[2]
 drying=vols[2]<vols[1]
 pivot=max(float(x["h"]) for x in rows[-21:-1])
 near_pivot=0<=((pivot-price)/pivot)<=.08
 breakout=price>pivot
 eligible=trend and within_high and above_low
 status="PIVOT_BREAKOUT_WATCH" if eligible and tightening and (near_pivot or breakout) else "VCP_CANDIDATE" if eligible and tightening else "TREND_WATCH" if eligible else "NOT_QUALIFIED"
 return {"status":status,"close":round(price,3),"sma50":round(m50,3),"sma150":round(m150,3),
  "sma200":round(m200,3),"pivot":round(pivot,3),"pivot_distance_pct":round((pivot-price)/pivot*100,2),
  "trend_template":trend,"within_25pct_high":within_high,"above_30pct_low":above_low,
  "contractions_pct":[round(x*100,2) for x in ranges],"contractions_narrowing":tightening,
  "volume_drying":drying,"volume_20d_avg":round(vols[2]),"bar_date_utc":datetime.fromtimestamp(rows[-1]["t"]/1000,timezone.utc).date().isoformat(),
  "pattern_method":"20-session nonoverlapping ranges, candidate heuristic, not a certified VCP",
  "buy_signal":False}

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  results=[];errors={}
  def fetch(ticker):return analyze(aggregates(ticker,"1d"))
  with ThreadPoolExecutor(max_workers=4) as executor:
   tasks={executor.submit(fetch,t):t for t in UNIVERSE}
   for future in as_completed(tasks):
    ticker=tasks[future]
    try:results.append({"ticker":ticker,**future.result()})
    except Exception as e:errors[ticker]=str(e)[:70]
  weight={"PIVOT_BREAKOUT_WATCH":0,"VCP_CANDIDATE":1,"TREND_WATCH":2,"NOT_QUALIFIED":3}
  results.sort(key=lambda x:(weight[x["status"]],abs(x["pivot_distance_pct"]),x["ticker"]))
  payload={"robot":"SORA-06","strategy":"Minervini inspired SEPA/VCP",
   "asof_utc":datetime.now(timezone.utc).isoformat(),"source":"Massive adjusted completed daily OHLCV",
   "scanned":len(UNIVERSE),"universe":list(UNIVERSE),
   "candidate_count":sum(x["status"] in ("PIVOT_BREAKOUT_WATCH","VCP_CANDIDATE") for x in results),
   "results":results,"errors":errors,"executed_paper_trades":0,
   "warning":"Trend/VCP heuristic only; relative strength, verified membership, actual intraday Smooth and risk approval not connected. No automatic BUY."}
  self.reply(200 if results else 503,payload)
 def reply(self,status,payload):
  body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
