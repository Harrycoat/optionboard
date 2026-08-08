"""
options_engine.py
------------------
Max Pain + Gamma Exposure(GEX) 계산 엔진.
Yahoo Finance(yfinance) 무료 옵션체인 데이터를 사용.

무료 소스 한계:
  - 실시간이 아닌 15~20분 지연 데이터일 수 있음
  - Greeks(gamma)를 직접 안 주는 경우가 많아 Black-Scholes로 자체 계산
  - 장중 호출량이 많으면 Yahoo 측에서 일시 차단(rate limit)될 수 있음
    -> 나중에 Polygon/Tradier 유료 API로 교체 시 이 파일의
       fetch_option_chain() 함수만 바꿔주면 나머지 로직은 그대로 재사용 가능
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import yfinance as yf
from scipy.stats import norm

RISK_FREE_RATE = 0.045  # 근사치, 필요시 실제 T-bill 금리로 교체 가능


# ---------------------------------------------------------------------------
# Black-Scholes Gamma (Yahoo가 gamma를 직접 안 줄 때 IV로부터 역산)
# ---------------------------------------------------------------------------
def bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float = RISK_FREE_RATE) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
        gamma = norm.pdf(d1) / (spot * iv * math.sqrt(t_years))
        return float(gamma)
    except (ValueError, ZeroDivisionError):
        return 0.0


def years_to_expiry(expiry_str: str) -> float:
    exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = max((exp_date - now).total_seconds() / 86400.0, 0.25)  # 최소 6시간짜리로 바닥
    return days / 365.0


# ---------------------------------------------------------------------------
# 데이터 가져오기
# ---------------------------------------------------------------------------
@dataclass
class OptionRow:
    strike: float
    call_oi: float
    put_oi: float
    call_iv: float
    put_iv: float


@dataclass
class ChainSnapshot:
    ticker: str
    spot: float
    expiry: str
    rows: list[OptionRow] = field(default_factory=list)


def fetch_option_chain(ticker: str, expiry: Optional[str] = None, max_expiries: int = 4) -> list[ChainSnapshot]:
    """
    가까운 만기(들)의 옵션체인을 가져온다.
    expiry가 주어지면 그 만기 하나만, 아니면 앞에서 max_expiries개를 합쳐서
    (GEX 표준 관례: 근접 만기 여러 개를 합산) 반환한다.
    """
    tk = yf.Ticker(ticker)
    spot = tk.fast_info.get("lastPrice") or tk.info.get("regularMarketPrice")
    if spot is None:
        raise ValueError(f"{ticker}: 현재가를 가져오지 못했습니다")

    all_expiries = tk.options
    if not all_expiries:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다 (옵션 미상장 종목일 수 있음)")

    expiries = [expiry] if expiry else list(all_expiries[:max_expiries])

    snapshots = []
    for exp in expiries:
        chain = tk.option_chain(exp)
        calls = chain.calls.set_index("strike")
        puts = chain.puts.set_index("strike")
        strikes = sorted(set(calls.index) | set(puts.index))

        rows = []
        for k in strikes:
            call_oi = float(calls.loc[k, "openInterest"]) if k in calls.index and not math.isnan(calls.loc[k, "openInterest"] or math.nan) else 0.0
            put_oi = float(puts.loc[k, "openInterest"]) if k in puts.index and not math.isnan(puts.loc[k, "openInterest"] or math.nan) else 0.0
            call_iv = float(calls.loc[k, "impliedVolatility"]) if k in calls.index else 0.0
            put_iv = float(puts.loc[k, "impliedVolatility"]) if k in puts.index else 0.0
            rows.append(OptionRow(strike=k, call_oi=call_oi, put_oi=put_oi, call_iv=call_iv, put_iv=put_iv))

        snapshots.append(ChainSnapshot(ticker=ticker, spot=float(spot), expiry=exp, rows=rows))

    return snapshots


# ---------------------------------------------------------------------------
# Max Pain
# ---------------------------------------------------------------------------
def compute_max_pain(rows: list[OptionRow]) -> dict:
    strikes = [r.strike for r in rows]
    pain_by_strike = {}

    for candidate in strikes:
        call_payout = sum((candidate - r.strike) * r.call_oi for r in rows if r.strike < candidate)
        put_payout = sum((r.strike - candidate) * r.put_oi for r in rows if r.strike > candidate)
        pain_by_strike[candidate] = (call_payout + put_payout) * 100  # 계약당 100주

    max_pain_strike = min(pain_by_strike, key=pain_by_strike.get)
    return {
        "max_pain_strike": max_pain_strike,
        "pain_curve": [{"strike": k, "total_pain": v} for k, v in sorted(pain_by_strike.items())],
    }


# ---------------------------------------------------------------------------
# GEX (Gamma Exposure)
# ---------------------------------------------------------------------------
def compute_gex(spot: float, expiry: str, rows: list[OptionRow]) -> dict:
    t = years_to_expiry(expiry)
    gex_by_strike = {}

    for r in rows:
        call_gamma = bs_gamma(spot, r.strike, t, r.call_iv) if r.call_iv > 0 else 0.0
        put_gamma = bs_gamma(spot, r.strike, t, r.put_iv) if r.put_iv > 0 else 0.0

        # 표준 컨벤션: 딜러는 콜 롱(양의 감마) / 풋 숏(음의 감마)로 모델링
        call_gex = call_gamma * r.call_oi * 100 * (spot ** 2) * 0.01
        put_gex = -1 * put_gamma * r.put_oi * 100 * (spot ** 2) * 0.01

        gex_by_strike[r.strike] = {
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": call_gex + put_gex,
        }

    if not gex_by_strike:
        return {"call_wall": None, "put_wall": None, "gamma_flip": None, "net_gex_total": 0, "by_strike": []}

    call_wall = max(gex_by_strike, key=lambda k: gex_by_strike[k]["call_gex"])
    put_wall = min(gex_by_strike, key=lambda k: gex_by_strike[k]["put_gex"])  # 가장 음수인 지점

    # Gamma flip: net GEX 누적합이 부호가 바뀌는 strike (낮은 strike부터 정렬 후 탐색)
    sorted_strikes = sorted(gex_by_strike.keys())
    cumulative = 0.0
    gamma_flip = None
    prev_strike = None
    for k in sorted_strikes:
        prev_cum = cumulative
        cumulative += gex_by_strike[k]["net_gex"]
        if prev_strike is not None and prev_cum < 0 <= cumulative:
            gamma_flip = k
            break
        prev_strike = k
    if gamma_flip is None and sorted_strikes:
        # 못 찾으면 net_gex 절대값이 0에 가장 가까운 strike로 근사
        gamma_flip = min(sorted_strikes, key=lambda k: abs(gex_by_strike[k]["net_gex"]))

    net_gex_total = sum(v["net_gex"] for v in gex_by_strike.values())

    return {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_flip": gamma_flip,
        "net_gex_total": net_gex_total,
        "regime": "positive" if net_gex_total > 0 else "negative",
        "by_strike": [
            {"strike": k, **v} for k, v in sorted(gex_by_strike.items())
        ],
    }


# ---------------------------------------------------------------------------
# 통합 분석
# ---------------------------------------------------------------------------
def analyze_ticker(ticker: str, expiry: Optional[str] = None) -> dict:
    ticker = ticker.upper().strip()
    snapshots = fetch_option_chain(ticker, expiry=expiry)

    # GEX는 근접 만기 합산 관례를 따르되, Max Pain은 가장 가까운 만기 기준으로 표시
    primary = snapshots[0]
    max_pain = compute_max_pain(primary.rows)

    # 여러 만기 합산 GEX (strike 기준 병합)
    merged: dict[float, OptionRow] = {}
    for snap in snapshots:
        for r in snap.rows:
            if r.strike not in merged:
                merged[r.strike] = OptionRow(r.strike, 0, 0, 0, 0)
            m = merged[r.strike]
            m.call_oi += r.call_oi
            m.put_oi += r.put_oi
            # IV는 근접 만기(첫 snapshot) 값을 우선 사용
            if m.call_iv == 0:
                m.call_iv = r.call_iv
            if m.put_iv == 0:
                m.put_iv = r.put_iv

    gex = compute_gex(primary.spot, primary.expiry, list(merged.values()))

    return {
        "ticker": ticker,
        "spot": primary.spot,
        "expiry_used": primary.expiry,
        "expiries_included_in_gex": [s.expiry for s in snapshots],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_pain": max_pain["max_pain_strike"],
        "pain_curve": max_pain["pain_curve"],
        "call_wall": gex["call_wall"],
        "put_wall": gex["put_wall"],
        "gamma_flip": gex["gamma_flip"],
        "net_gex_total": gex["net_gex_total"],
        "regime": gex["regime"],
        "gex_by_strike": gex["by_strike"],
    }


if __name__ == "__main__":
    import json
    import sys

    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    result = analyze_ticker(t)
    print(json.dumps({k: v for k, v in result.items() if k not in ("pain_curve", "gex_by_strike")}, indent=2))
