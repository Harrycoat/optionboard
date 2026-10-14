"""SORA paper-only buy ledger. Pure computation; caller must persist results atomically.
Never places orders, fetches quotes, or fabricates prices.
"""
from __future__ import annotations
from datetime import datetime, timezone
from copy import deepcopy
import math
import re

def _number(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def execute_paper_buy(state, order, *, allowed_symbols):
    """Return updated immutable-by-contract state and receipt after validating a verified signal.
    Caller MUST persist the returned state to a durable store before reporting execution.
    """
    out=deepcopy(state)
    accounts=out.get("accounts",[])
    target=next((a for a in accounts if a.get("id")==order.get("account_id")),None)
    if target is None: raise ValueError("UNKNOWN_ACCOUNT")
    ticker=str(order.get("ticker","")).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",ticker):raise ValueError("INVALID_TICKER")
    if ticker not in set(allowed_symbols):raise ValueError("INDEX_MEMBERSHIP_NOT_VERIFIED")
    if order.get("classification")!="ENTRY":raise ValueError("NO_VERIFIED_ENTRY")
    if order.get("signal_source")!="swing_signals.classify":raise ValueError("INVALID_SIGNAL_SOURCE")
    if order.get("data_status")!="VERIFIED_COMPLETED_BARS":raise ValueError("UNVERIFIED_DATA")
    if not order.get("daily_sma50_rising") or not order.get("intraday_30m_ready"):raise ValueError("ENTRY_CRITERIA_NOT_MET")
    price,quantity=order.get("fill_price"),order.get("quantity")
    if not _number(price) or price<=0 or not isinstance(quantity,int) or quantity<=0:raise ValueError("INVALID_SIZE_OR_PRICE")
    ts=order.get("fill_time_utc");bar_end=order.get("signal_bar_end_utc")
    try:
        fill=datetime.fromisoformat(ts.replace("Z","+00:00"))
        ended=datetime.fromisoformat(bar_end.replace("Z","+00:00"))
    except (ValueError,AttributeError):raise ValueError("INVALID_TIMESTAMP")
    if fill.tzinfo is None or ended.tzinfo is None or ended>fill:raise ValueError("SIGNAL_AFTER_FILL")
    if not order.get("data_provenance"):raise ValueError("MISSING_PROVENANCE")
    if not order.get("reason"):raise ValueError("MISSING_REASON")
    existing=target.setdefault("trades",[])
    identity=f'{target["id"]}:BUY:{ticker}:{fill.isoformat()}'
    if any(x.get("id")==identity for x in existing):raise ValueError("DUPLICATE_TRADE")
    positions=target.setdefault("positions",[])
    if any(p.get("ticker")==ticker for p in positions):raise ValueError("ALREADY_OWNED")
    if len(positions)>=out.get("rules",{}).get("maxPositions",12):raise ValueError("MAX_POSITIONS")
    core=ticker in ("NVDA","AMZN","PLTR")
    if core and sum(p.get("bucket")=="CORE" for p in positions)>=3:raise ValueError("CORE_LIMIT")
    if not core and sum(p.get("bucket")=="SWING" for p in positions)>=9:raise ValueError("SWING_LIMIT")
    cash=target.get("cash",0)
    if not _number(cash) or cash<0:raise ValueError("INVALID_CASH")
    cost=round(price*quantity,2)
    if cost>cash+1e-6:raise ValueError("INSUFFICIENT_CASH")
    # Initial allocation budget: max 20% of starting capital for all SWING buys.
    # Avoid spending a realized CORE gain or cash transferred from another account.
    initial=float(target.get("initialCash",100000))
    allocated=sum(p["entry_price"]*p["quantity"] for p in positions if p.get("bucket")=="SWING")
    if not core and allocated+cost>initial*.20+1e-6:raise ValueError("SWING_BUDGET_EXCEEDED")
    receipt={"id":identity,"side":"BUY","ticker":ticker,"quantity":quantity,"fill_price":price,
             "fill_time_utc":fill.isoformat(),"signal_bar_end_utc":ended.isoformat(),
             "signal_source":order["signal_source"],"data_provenance":order["data_provenance"],
             "reason":order["reason"],"bucket":"CORE" if core else "SWING",
             "mode":"PAPER","status":"PENDING_DURABLE_COMMIT"}
    positions.append({"ticker":ticker,"bucket":receipt["bucket"],"quantity":quantity,
                      "entry_price":price,"entry_time_utc":receipt["fill_time_utc"]})
    existing.append(receipt)
    target["cash"]=round(cash-cost,2)
    target["status"]="PAPER_ACTIVE"
    return out,receipt
