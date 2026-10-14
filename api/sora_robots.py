"""SORA 30 independent paper robots: strategy routing + fail-closed decision engine.

This is a deterministic evaluation engine, NOT an automated trader.
No price fabrication, no actual brokerage orders, no TOS Smooth substitution.
Every account evaluates independently; entries require certified input.
"""
from datetime import datetime, timezone
import math

def _isnum(x):
    return type(x) in (float,int) and math.isfinite(x)

def setup_for(account):
    """Keep the 30 experiment descriptions, allocating different setup families."""
    n=int(account["id"].split("-")[-1])
    if n<=5: return "CORE_20_50"
    if n<=14:return "SECTOR_LEADER"
    if n==15:return "PULLBACK_10"
    if n==16:return "PULLBACK_20"
    if n==17:return "PULLBACK_50"
    if n==18:return "HULL21_REENTRY"
    if n==19:return "MA_ALIGNMENT"
    if n==20:return "PIVOT_BREAKOUT"
    if n==21:return "EARNINGS_GAP"
    if n==22:return "RSI_DCA_5"
    if n<=26:return "COMBINED_CORE"
    if n==27:return "VCP_BREAKOUT"
    if n==28:return "COMBINED_FULL"
    if n==29:return "QLD_QQQ_SMA50_DCA"
    return "TQQQ_QQQ_SMA200_DCA"

def evaluate(account, candidate=None, *, verified_members=None):
    """One independent robot decision; only verified inputs may produce ENTRY_ELIGIBLE.

    Inputs are deliberately not assumed to be true when missing.
    No code path executes an order; approval, sizing and durable storage are separate.
    """
    robot=account["id"]
    strategy=setup_for(account)
    result={"account_id":robot,"strategy":strategy,"status":"WAIT",
            "reason":"검증된 완료봉 시장 데이터와 전략별 신호 필요","ticker":None,
            "paper_trade_executed":False}
    if not candidate:
        return result
    ticker=candidate.get("ticker")
    result["ticker"]=ticker
    if strategy in ("QLD_QQQ_SMA50_DCA","TQQQ_QQQ_SMA200_DCA"):
        expected="QLD" if strategy.startswith("QLD") else "TQQQ"
        if ticker!=expected:return {**result,"reason":"해당 DCA 로봇의 전용 ETF가 아님"}
        if not candidate.get("qqq_daily_verified"):return {**result,"reason":"QQQ 일봉 검증 필요"}
        ma="sma50_touch" if expected=="QLD" else "sma200_touch"
        if not candidate.get(ma):return {**result,"reason":"QQQ 기준 이동평균선 터치 대기"}
        return {**result,"status":"WATCH","reason":"분할매수 차수·하락일·종가·예산 미검증; 자동매수 금지"}
    if not isinstance(ticker,str) or verified_members is None or ticker not in verified_members:
        return {**result,"reason":"S&P500/Nasdaq100 구성 확인 전"}
    if not candidate.get("completed_daily") or not candidate.get("completed_30m"):
        return {**result,"reason":"완료된 일봉·30분봉 필요"}
    if not candidate.get("sma50_rising"):
        return {**result,"reason":"일봉 50일선 상승 미확인"}
    if strategy in ("CORE_20_50","COMBINED_CORE") or (strategy=="COMBINED_FULL" and candidate.get("core_candidate")):
        if not (candidate.get("touch_sma20") or candidate.get("touch_sma50")):
            return {**result,"reason":"CORE 20일선/50일선 눌림 대기"}
    elif strategy=="PULLBACK_10" and not candidate.get("touch_sma10"):
        return {**result,"reason":"일봉 10일선 눌림 대기"}
    elif strategy=="PULLBACK_20" and not candidate.get("touch_sma20"):
        return {**result,"reason":"일봉 20일선 눌림 대기"}
    elif strategy=="PULLBACK_50" and not candidate.get("touch_sma50"):
        return {**result,"reason":"일봉 50일선 눌림 대기"}
    else:
        # Non-pullback strategies require their OWN confirmed setup, not a generic 20MA proxy.
        if strategy not in ("CORE_20_50","COMBINED_CORE","PULLBACK_10","PULLBACK_20","PULLBACK_50"):
            if candidate.get("verified_setup_family")!=strategy:
                return {**result,"reason":strategy+" 고유 전략 신호 미검증"}
    if not candidate.get("sell_pressure_easing"):
        return {**result,"reason":"매도세 둔화 미확인"}
    if not candidate.get("reference_high_break"):
        return {**result,"status":"WATCH","reason":"30분봉 기준봉 고점 돌파 대기"}
    if not candidate.get("smooth_up") or candidate.get("smooth_source")!="VERIFIED_TOS":
        return {**result,"status":"WATCH","reason":"실제 TOS Smooth 상승 검증 전 (EVP 추정치 대체 금지)"}
    if not candidate.get("bar_provenance") or not candidate.get("signal_time_utc"):
        return {**result,"status":"WATCH","reason":"시세 출처·신호 시각 미검증"}
    try:
        stamp=datetime.fromisoformat(candidate["signal_time_utc"].replace("Z","+00:00"))
        if stamp.tzinfo is None:raise ValueError()
    except (TypeError,AttributeError,ValueError):
        return {**result,"status":"WATCH","reason":"시그널 타임스탬프 검증 실패"}
    return {**result,"status":"ENTRY_ELIGIBLE","reason":"해당 로봇의 진입 조건 확인; 가상체결·예산·저장 별도 검사 필요"}

def evaluate_all(config, candidates=None, verified_members=None):
    accounts=config["accounts"]
    if len(accounts)!=30 or len({a["id"] for a in accounts})!=30:
        raise ValueError("EXPECTED_30_DISTINCT_ACCOUNTS")
    candidates=candidates or {}
    return {"mode":"SIMULATION_ONLY","paper_trades_executed":0,
            "robots":[evaluate(a,candidates.get(a["id"]),verified_members=verified_members) for a in accounts]}
