"""SORA paper-only daily-close exit engine.

Pure functions. No broker orders, no quotes, no database writes.
Caller must persist receipts atomically before claiming execution.
20MA first breach -> half of then-held shares (rounded up);
50MA breach -> all remaining shares. No hedging in standard robots.
"""
from copy import deepcopy
from datetime import datetime
from math import ceil, isfinite

def _positive(x):return type(x) in (int,float) and isfinite(x) and x>0
def _stamp(x):
 try:
  y=datetime.fromisoformat(x.replace("Z","+00:00"))
  if y.tzinfo is None:raise ValueError()
  return y
 except (ValueError,AttributeError):raise ValueError("INVALID_TIME") from None

def plan_exit(positions, bars, prior_sales=()):
 """Use only last verified completed daily close, not a live/incomplete bar. One ticker per evaluation."""
 if len(positions)!=1:raise ValueError("SINGLE_TICKER_EVALUATION_ONLY")
 if len(bars)<51 or any(not b.get("complete") or not _positive(b.get("c")) for b in bars[-51:]):
  raise ValueError("COMPLETED_DAILY_BARS_REQUIRED")
 closes=[b["c"] for b in bars]
 close=closes[-1];ma20=sum(closes[-20:])/20;ma50=sum(closes[-50:])/50
 previous=closes[-2];p20=sum(closes[-21:-1])/20;p50=sum(closes[-51:-1])/50
 if not all(_positive(x) for x in (ma20,ma50,p20,p50)):raise ValueError("INVALID_MA")
 # Below-MA state is actionable even when first scan missed exact crossing.
 # Persist exit stages to prevent repetitive half sells.
 decisions=[]
 for p in positions:
  qty=p.get("quantity")
  if type(qty)!=int or qty<=0:continue
  ticker=p["ticker"]
  stage50=any(s.get("ticker")==ticker and s.get("exit_stage")=="SMA50_FULL" for s in prior_sales)
  stage20=any(s.get("ticker")==ticker and s.get("exit_stage")=="SMA20_HALF" for s in prior_sales)
  if close<ma50 and not stage50:
   decisions.append({"ticker":ticker,"quantity":qty,"exit_stage":"SMA50_FULL","reason":"완료 일봉 종가 50일선 하향 이탈 · 잔여 전량 가상청산"})
  elif close<ma20 and not stage20:
   decisions.append({"ticker":ticker,"quantity":ceil(qty/2),"exit_stage":"SMA20_HALF","reason":"완료 일봉 종가 20일선 하향 이탈 · 보유량 절반 가상청산"})
 return {"close":close,"sma20":ma20,"sma50":ma50,"previous_close":previous,"decisions":decisions,"mode":"PAPER_ONLY"}

def apply_paper_exit(state, account_id, proposal, *, fill_price,fill_time_utc,signal_bar_end_utc,provenance,request_id):
 """Produce pending ledger state. Never claim SAVED until durable external commit."""
 if not _positive(fill_price) or not provenance or not request_id:raise ValueError("UNVERIFIED_FILL")
 fill=_stamp(fill_time_utc);signal=_stamp(signal_bar_end_utc)
 if signal>fill:raise ValueError("FUTURE_SIGNAL")
 if proposal.get("exit_stage") not in ("SMA20_HALF","SMA50_FULL"):raise ValueError("INVALID_EXIT_STAGE")
 out=deepcopy(state)
 a=next((v for v in out.get("accounts",[]) if v.get("id")==account_id),None)
 if not a:raise ValueError("ACCOUNT_NOT_FOUND")
 if any(t.get("request_id")==request_id for t in a.get("trades",[])):raise ValueError("DUPLICATE_REQUEST")
 symbol=proposal["ticker"];pos=next((p for p in a.get("positions",[]) if p.get("ticker")==symbol),None)
 qty=proposal.get("quantity")
 if pos is None or type(qty)!=int or qty<=0 or qty>pos.get("quantity",0):raise ValueError("INVALID_EXIT_QUANTITY")
 original_qty=pos["quantity"]
 if proposal["exit_stage"]=="SMA20_HALF" and qty!=ceil(original_qty/2):raise ValueError("HALF_SIZE_REQUIRED")
 if proposal["exit_stage"]=="SMA50_FULL" and qty!=original_qty:raise ValueError("FULL_SIZE_REQUIRED")
 if proposal["exit_stage"]=="SMA20_HALF" and any(t.get("ticker")==symbol and t.get("exit_stage")=="SMA20_HALF" for t in a.get("trades",[])):
  raise ValueError("DUPLICATE_HALF_EXIT")
 proceeds=round(qty*fill_price,2)
 pos["quantity"]-=qty
 if not pos["quantity"]:a["positions"].remove(pos)
 a["cash"]=round(a["cash"]+proceeds,2)
 receipt={"request_id":request_id,"ticker":symbol,"side":"SELL","quantity":qty,"fill_price":fill_price,
  "fill_time_utc":fill.isoformat(),"signal_bar_end_utc":signal.isoformat(),"data_provenance":provenance,
  "exit_stage":proposal["exit_stage"],"reason":proposal["reason"],"mode":"PAPER",
  "status":"PENDING_DURABLE_COMMIT","proceeds":proceeds}
 a.setdefault("trades",[]).append(receipt)
 return out,receipt
