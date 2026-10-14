"""SORA ten-robot opportunity engine (research only, no orders).

Evaluates independent setups against verified completed market bars.
Broad WATCH/READY discovery avoids hiding candidates, while ENTRY is fail-closed.
No quotes fetched, no fills produced, no ledger mutation.
"""
from datetime import datetime,timezone
import math

STRATEGIES={
 "SORA-01":"CORE_MANAGER_AI",
 "SORA-02":"CORE80_SWING20",
 "SORA-03":"BIGTECH_20_50_DCA",
 "SORA-04":"GROWTH_50MA",
 "SORA-05":"HULL21_SMOOTH",
 "SORA-06":"VCP_PIVOT",
 "SORA-07":"SECTOR_ROTATION",
 "SORA-08":"GEX_WALL",
 "SORA-09":"QLD_QQQ_SMA50_DCA",
 "SORA-10":"TQQQ_QQQ_SMA200_DCA",
}
CORE=frozenset(("NVDA","AMZN","PLTR","TSLA","AAPL","META","MSFT","COST"))
def _numeric(x):
 return type(x) in (int,float) and math.isfinite(x)
def _mean(xs):return sum(xs)/len(xs)
def _ma(closes,n):return _mean(closes[-n:])
def _bar_ok(b):
 return (isinstance(b,dict) and all(_numeric(b.get(k)) for k in ("o","h","l","c","v"))
         and b["l"]<=min(b["o"],b["c"])<=max(b["o"],b["c"])<=b["h"]
         and b["v"]>=0 and b.get("complete") is True)

def daily_features(bars):
 """All bars must be chronological, completed, from one certified source."""
 if len(bars)<205 or not all(_bar_ok(x) for x in bars):raise ValueError("INVALID_OR_INCOMPLETE_DAILY_BARS")
 times=[x.get("t") for x in bars]
 if any(not _numeric(t) for t in times) or any(a>=b for a,b in zip(times,times[1:])):
  raise ValueError("UNORDERED_BARS")
 closes=[b["c"] for b in bars]
 ma20=_ma(closes,20);ma50=_ma(closes,50);ma50past=_mean(closes[-55:-5])
 ma200=_ma(closes,200)
 last=bars[-1];price=last["c"]
 def near(n):return abs(price/n-1)<=.035 or last["l"]<=n<=last["h"]
 # The broader WATCH band of 3.5% is not an ENTRY or a claim of a touch.
 vol20=_mean([b["v"] for b in bars[-21:-1]])
 return {"price":price,"sma20":ma20,"sma50":ma50,"sma200":ma200,
    "sma50_rising":ma50>ma50past,"sma200_rising":ma200>_mean(closes[-205:-5]),
    "near20":near(ma20),"near50":near(ma50),
    "touch20":last["l"]<=ma20<=last["h"],
    "touch50":last["l"]<=ma50<=last["h"],
    "volume_ratio":last["v"]/max(vol20,1),
    "above50":price>=ma50,"high20_prior":max(b["h"] for b in bars[-21:-1]),
    "bar_timestamp":last["t"]}

def decide(robot_id,ticker,feat, *, verified_member=False,context=None):
 """WATCH opportunities are plentiful, but never represented as executed buys."""
 if robot_id not in STRATEGIES:raise ValueError("UNKNOWN_ROBOT")
 context=context or {}
 result={"account_id":robot_id,"strategy":STRATEGIES[robot_id],
   "ticker":ticker,"status":"WAIT","reason":"전략 조건 대기","paper_trade_executed":False}
 def put(status,why):return {**result,"status":status,"reason":why}
 if robot_id in ("SORA-09","SORA-10"):
  target="QLD" if robot_id=="SORA-09" else "TQQQ"
  if ticker!=target:return put("WAIT","전용 레버리지 ETF 종목 아님")
  ma="qqq_sma50_touch" if robot_id=="SORA-09" else "qqq_sma200_touch"
  if not context.get("qqq_daily_verified"):return put("WAIT","QQQ 완료 일봉 검증 전")
  if not context.get(ma):return put("WATCH","QQQ 이동평균선 접근 감시")
  return put("READY","ETF 분할매수 관찰; 하락일·차수·자금 한도·종가 체결 검증 필요")
 if not verified_member:return put("WAIT","지수 구성원 검증 전")
 if not feat or not feat.get("sma50_rising"):return put("WAIT","일봉 상승 추세 조건 대기")
 strategy=STRATEGIES[robot_id]
 if robot_id=="SORA-01" and ticker not in CORE:return put("WAIT","CORE 후보 종목 아님")
 if strategy in ("CORE_MANAGER_AI","CORE80_SWING20","BIGTECH_20_50_DCA"):
  if strategy=="BIGTECH_20_50_DCA" and ticker not in CORE:return put("WAIT","빅테크 CORE 후보 아님")
  if not (feat["near20"] or feat["near50"]):return put("WAIT","20일선·50일선 눌림 대기")
  tag="20일선" if feat["near20"] else "50일선"
 elif strategy=="GROWTH_50MA":
  if not feat["near50"]:return put("WAIT","성장주 50일선 접근 전")
  tag="성장주 50일선"
 elif strategy=="HULL21_SMOOTH":
  if not context.get("hull21_reentry"):return put("WAIT","Hull21 재진입 대기")
  tag="Hull21 회복"
 elif strategy=="VCP_PIVOT":
  if not context.get("verified_vcp"):return put("WAIT","검증된 VCP 패턴 없음")
  tag="VCP 돌파"
 elif strategy=="SECTOR_ROTATION":
  if not context.get("verified_sector_leader"):return put("WAIT","강한 섹터 주도주 검증 전")
  tag="섹터 주도주"
 elif strategy=="GEX_WALL":
  if not context.get("verified_wall_near"):return put("WAIT","Call/Put Wall 접근 검증 전")
  tag="GEX Wall"
 else: return put("WAIT","전략 설정 미확인")
 if not context.get("completed_30m"):
  return put("WATCH",tag+" 발견 · 완료 30분봉 대기")
 if not context.get("selling_easing"):
  return put("WATCH",tag+" 발견 · 매도 압력 둔화 대기")
 if not context.get("reference_high_break"):
  return put("READY",tag+" · 매도 압력 둔화, 기준봉 고점 돌파 대기")
 if not (context.get("tos_smooth_verified") and context.get("tos_smooth_up")):
  return put("READY",tag+" · 기준봉 돌파, 실제 TOS Smooth 검증 대기")
 if not context.get("risk_plan_verified") or not context.get("source_provenance"):
  return put("READY",tag+" · 손절·포지션 크기·데이터 출처 검증 대기")
 return put("ENTRY_ELIGIBLE",tag+" · 조건 확인, 가상체결과 영구 기록은 별도 엔진에서 수행")

def rank_watch(offers,limit=30):
 priority={"ENTRY_ELIGIBLE":0,"READY":1,"WATCH":2,"WAIT":3}
 return sorted(offers,key=lambda x:(priority.get(x["status"],9),x["account_id"],x["ticker"]))[:limit]

def risk_size(cash,entry,stop, *, equity=100000,risk_fraction=.005,max_position_fraction=.10):
 """Position cap and stop risk, not advice or guaranteed loss cap."""
 if not all(_numeric(x) for x in (cash,entry,stop,equity,risk_fraction,max_position_fraction)):
  raise ValueError("BAD_POSITION_INPUT")
 if not(0<stop<entry and cash>=0 and equity>0 and 0<risk_fraction<=.02 and 0<max_position_fraction<=1):
  raise ValueError("BAD_RISK_CONFIGURATION")
 by_risk=math.floor(equity*risk_fraction/(entry-stop))
 by_cap=math.floor(equity*max_position_fraction/entry)
 by_cash=math.floor(cash/entry)
 return max(0,min(by_risk,by_cap,by_cash))
