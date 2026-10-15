"""Research-only strategy scanner for SORA 01-05,07-10.
Uses completed adjusted daily bars; no paper orders or unverifiable signals.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json,os,sys
sys.path.insert(0,os.path.dirname(__file__))
from swing_radar_chart import aggregates
from sora_ten_opportunity_engine import daily_features

CORE=("NVDA","AMZN","TSLA","AAPL","MSFT","META","PLTR","COST")
LEADERS=("NVDA","AVGO","AMD","MU","META","AMZN","VRT","PLTR")
UNIVERSES={
 "SORA-01":CORE,"SORA-02":LEADERS,"SORA-03":CORE,
 "SORA-04":LEADERS,"SORA-05":LEADERS,"SORA-07":LEADERS,
 "SORA-08":CORE,"SORA-09":("QQQ","QLD"),"SORA-10":("QQQ","TQQQ")}
def analyze(robot,rows,qqq=None):
 f=daily_features(rows); closes=[x["c"] for x in rows]
 price=f["price"]; m10=sum(closes[-10:])/10;m20=f["sma20"];m50=f["sma50"]
 prior10=sum(closes[-11:-1])/10;prior20=sum(closes[-21:-1])/20
 cross1020=prior10<=prior20 and m10>m20
 pivot=f["high20_prior"];distance=(pivot-price)/pivot*100
 status="NO_SETUP";reason="전략 조건 미충족"
 if robot in ("SORA-01","SORA-02","SORA-03"):
  if f["sma50_rising"] and (f["near20"] or f["near50"]):
   status="WATCH";reason="상승 50일선 + 20/50일선 눌림 후보"
 elif robot=="SORA-04":
  if cross1020 and m10<m50 and m20<m50:
   status="WATCH";reason="10/20 골든크로스 1차 후보 · 50일선 위 저항"
  elif m10>m20>m50 and prior20<=sum(closes[-51:-1])/50:
   status="WATCH";reason="10/20/50선 정렬 2차 후보"
  elif m10>m20>m50 and price>=pivot:
   status="WATCH";reason="20일 고점 돌파 3차 피벗 후보(정식 피벗 미검증)"
 elif robot=="SORA-05":
  if f["sma50_rising"] and f["near20"]:
   status="WATCH";reason="20일선 접근 · Hull21과 실제 Smooth 확인 필요"
 elif robot=="SORA-07":
  if f["sma50_rising"] and price>m20 and f["volume_ratio"]>=1:
   status="WATCH";reason="추세·거래량 후보 · 섹터 상대강도 검증 필요"
 elif robot=="SORA-08":
  if f["sma50_rising"] and (f["near20"] or f["near50"]):
   status="WATCH";reason="추세 눌림 후보 · Call/Put Wall 수치 미검증"
 elif robot in ("SORA-09","SORA-10"):
  if qqq is not None and qqq.get("complete") and qqq.get("touch"):
   status="WATCH";reason="QQQ 이동평균선 터치 후보 · 분할매수 위험검증 필요"
  else:reason="QQQ 이동평균선 터치 조건 대기"
 return {"status":status,"reason":reason,"close":round(price,4),"sma10":round(m10,4),
  "sma20":round(m20,4),"sma50":round(m50,4),"volume_ratio":round(f["volume_ratio"],2),
  "cross_10_20":cross1020,"pivot_20day":round(pivot,4),"pivot_distance_pct":round(distance,2),
  "bar_date_utc":datetime.fromtimestamp(rows[-1]["t"]/1000,timezone.utc).date().isoformat(),
  "buy_signal":False}

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  robot=parse_qs(urlparse(self.path).query).get("robot",[""])[0]
  if robot not in UNIVERSES:return self.reply(400,{"error":"INVALID_ROBOT"})
  universe=UNIVERSES[robot];results=[];errors={};data={}
  with ThreadPoolExecutor(max_workers=4) as pool:
   tasks={pool.submit(aggregates,sym,"1d"):sym for sym in universe}
   for task in as_completed(tasks):
    sym=tasks[task]
    try:data[sym]=task.result()
    except Exception as e:errors[sym]=type(e).__name__
  qqq=None
  if robot in ("SORA-09","SORA-10") and "QQQ" in data:
   try:
    r=data["QQQ"];f=daily_features(r);level=f["sma50"] if robot=="SORA-09" else f["sma200"]
    qqq={"complete":True,"touch":r[-1]["l"]<=level<=r[-1]["h"],
         "reference":round(level,4)}
   except Exception:pass
  for sym,rows in data.items():
   if robot in ("SORA-09","SORA-10") and sym=="QQQ":continue
   try:results.append({"ticker":sym,**analyze(robot,rows,qqq)})
   except Exception as e:errors[sym]=type(e).__name__
  results.sort(key=lambda x:(x["status"]!="WATCH",x["ticker"]))
  return self.reply(200 if results else 503,{
   "robot":robot,"mode":"RESEARCH_ONLY","source":"Massive adjusted completed daily OHLCV",
   "asof_utc":datetime.now(timezone.utc).isoformat(),"scanned":len(universe),
   "candidate_count":sum(x["status"]=="WATCH" for x in results),
   "results":results,"errors":errors,"qqq_reference":qqq,
   "paper_trades_executed":0,
   "warning":"WATCH is not verified entry. Intraday TOS Smooth, true sector RS, GEX Walls, index membership and volume breakout confirmation must be separately verified."})
 def reply(self,status,obj):
  body=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
  self.end_headers();self.wfile.write(body)
