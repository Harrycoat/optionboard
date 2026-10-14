"""SORA educational paper-entry gate: never executes real orders.
Daily SMA20/SMA50 pullback + easing selling pressure + completed 30m turn and price confirmation.
Smooth represented by OHLCV EVP proxy unless a verified real feed is explicitly supplied.
"""
def assess_entry(daily, m30, *, constituent_verified=False):
    if not constituent_verified:
        return {"status":"WAIT","reason":"S&P 500/Nasdaq 100 구성 확인 전"}
    if not daily.get("sma50_rising"):
        return {"status":"WAIT","reason":"일봉 50일선 상승 조건 미충족"}
    if not (daily.get("near_sma20") or daily.get("near_sma50")):
        return {"status":"WAIT","reason":"일봉 20일선·50일선 부근 아님"}
    if not daily.get("sell_pressure_easing"):
        return {"status":"WAIT","reason":"일봉 매도 압력 둔화 미확인"}
    if not m30.get("completed"):
        return {"status":"WAIT","reason":"완료된 30분봉 데이터 필요"}
    if not m30.get("smooth_turn_up"):
        return {"status":"WATCH","reason":"30분봉 Smooth 상승 전환 대기"}
    if not m30.get("reference_high_break"):
        return {"status":"WATCH","reason":"30분봉 기준봉 고점 돌파 대기"}
    if m30.get("smooth_source") != "VERIFIED_TOS":
        return {"status":"WATCH","reason":"OHLCV 기반 Smooth 추정치만 확인됨; 실제 Smooth 추가 확인 필요"}
    return {"status":"ENTRY","reason":"일봉 20/50MA 눌림+매도 둔화, 완료된 30분봉 Smooth 전환+기준봉 돌파 확인"}
