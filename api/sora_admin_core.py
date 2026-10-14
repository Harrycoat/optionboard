"""Administrative CORE paper-buy input validation.
Pure function; NO route, authorization bypass, broker order, or persistence.
An authenticated admin server and durable ledger are required before enabling.
"""
from datetime import datetime
from math import isfinite

CORE_SYMBOLS=frozenset(("NVDA","AMZN","PLTR"))
def validate_admin_core_buy(record):
    if record.get("source")!="ADMIN" or record.get("mode")!="PAPER":
        raise ValueError("PAPER_ADMIN_ONLY")
    ticker=str(record.get("ticker","")).upper()
    if ticker not in CORE_SYMBOLS:raise ValueError("NOT_APPROVED_CORE")
    account=str(record.get("account_id",""))
    if account not in {f"SORA-{i:02d}" for i in range(1,29)}:
        raise ValueError("INVALID_CORE_ACCOUNT")
    qty=record.get("quantity")
    price=record.get("fill_price")
    if not isinstance(qty,int) or isinstance(qty,bool) or qty<=0:
        raise ValueError("INVALID_QUANTITY")
    if isinstance(price,bool) or not isinstance(price,(int,float)) or not isfinite(price) or price<=0:
        raise ValueError("INVALID_PRICE")
    ts=record.get("fill_time_utc")
    try: dt=datetime.fromisoformat(ts.replace("Z","+00:00"))
    except (AttributeError,ValueError):raise ValueError("INVALID_TIME") from None
    if dt.tzinfo is None:raise ValueError("TIME_ZONE_REQUIRED")
    stage=record.get("entry_stage")
    if stage not in ("20MA_FIRST","50MA_SECOND","MANUAL_OTHER"):
        raise ValueError("INVALID_STAGE")
    reason=str(record.get("reason") or "").strip()
    if not reason:raise ValueError("REASON_REQUIRED")
    return {"account_id":account,"ticker":ticker,"quantity":qty,"fill_price":round(float(price),4),
            "fill_time_utc":dt.isoformat(),"entry_stage":stage,"reason":reason,
            "source":"ADMIN","mode":"PAPER","status":"VALIDATED_NOT_SAVED"}
