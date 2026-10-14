"""Read-only SORA paper-trade entry analysis. No fabricated fills or future leakage.
Input records must come from a verified, durably persisted trade ledger.
"""
from collections import defaultdict
from statistics import mean, median

HORIZONS=(5,10,20)
def summarize_entry_performance(records, min_sample=5):
    groups=defaultdict(list)
    for r in records:
        if r.get("mode")!="PAPER" or r.get("status")!="COMMITTED":continue
        if r.get("side")!="BUY" or not r.get("data_provenance"):continue
        entry=r.get("entry_context") or {}
        if not entry.get("observed_at_utc") or not entry.get("setup"):continue
        # No theoretical backfilled trade, no unverified price/return.
        for horizon in HORIZONS:
            item=(r.get("forward_returns") or {}).get(str(horizon))
            if not isinstance(item,dict) or item.get("verified") is not True:continue
            ret=item.get("return_pct")
            if isinstance(ret,bool) or not isinstance(ret,(int,float)) or not (-100<=ret<=100000):continue
            if not item.get("source") or not item.get("price_date"):continue
            groups[(entry["setup"],horizon)].append(float(ret))
    result=[]
    for (setup,horizon),vals in sorted(groups.items()):
        n=len(vals)
        result.append({"setup":setup,"horizon_sessions":horizon,"count":n,
            "avg_return_pct":round(mean(vals),3) if n>=min_sample else None,
            "median_return_pct":round(median(vals),3) if n>=min_sample else None,
            "win_rate_pct":round(100*sum(v>0 for v in vals)/n,2) if n>=min_sample else None,
            "status":"SUFFICIENT_SAMPLE" if n>=min_sample else "INSUFFICIENT_SAMPLE"})
    return {"method":"entry_cohort_forward_return","minimum_sample":min_sample,
        "note":"Forward returns measured from recorded paper fill to verified completed future sessions; no causal inference or guarantee.","groups":result}
