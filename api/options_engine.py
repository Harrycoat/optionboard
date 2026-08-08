"""
options_engine.py
------------------
Max Pain + Gamma Exposure(GEX) 계산 엔진.
Massive.com(구 Polygon.io) 유료 옵션체인 API 사용 (Options Starter 플랜, 15분 지연).

Massive API가 계약별 실측 Greeks(gamma 포함), IV, OI를 직접 제공하므로
예전 야후 무료 버전처럼 Black-Scholes로 gamma를 자체 역산할 필요가 없음.
-> 계산 정확도가 바챠트 같은 유료 소스와 훨씬 가까워짐.

필요 환경변수:
  MASSIVE_API_KEY  (Vercel Environment Variables에 등록)
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

MASSIVE_API_BASE = "https://api.massive.com"
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY")


class MassiveAPIError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 데이터 가져오기 (Massive.com REST API)
# ---------------------------------------------------------------------------
@dataclass
class OptionRow:
    strike: float
    call_oi: float
    put_oi: float
    call_gamma: float
    put_gamma: float


@dataclass
class ChainSnapshot:
    ticker: str
    spot: float
    expiry: str
    rows: list[OptionRow] = field(default_factory=list)


def _massive_get(url: str, params: Optional[dict] = None) -> dict:
    if not MASSIVE_API_KEY:
        raise MassiveAPIError(
            "MASSIVE_API_KEY 환경변수가 설정되지 않았습니다. "
            "Vercel 프로젝트 Settings > Environment Variables에 등록해주세요."
        )
    params = dict(params or {})
    params["apiKey"] = MASSIVE_API_KEY
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        raise MassiveAPIError(f"Massive API 오류 ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if data.get("status") not in ("OK", "DELAYED"):
        raise MassiveAPIError(f"Massive API 응답 오류: {data}")
    return data


def _fetch_full_options_chain(ticker: str) -> list[dict]:
    """
    /v3/snapshot/options/{ticker} 전체 결과를 페이지네이션 따라가며 수집.
    (모든 만기 + 모든 스트라이크가 한 번에 옴, 이후 만기별로 그룹핑)
    """
    url = f"{MASSIVE_API_BASE}/v3/snapshot/options/{ticker}"
    all_results: list[dict] = []
    params = {"limit": 250}
    next_url = None

    while True:
        data = _massive_get(next_url or url, params if not next_url else None)
        all_results.extend(data.get("results", []))
        next_url = data.get("next_url")
        if not next_url:
            break
        # next_url에는 이미 apiKey 빼고 다른 쿼리들이 포함돼 있어서, apiKey만 다시 붙여준다
        next_url = next_url + ("&" if "?" in next_url else "?") + f"apiKey={MASSIVE_API_KEY}"

    if not all_results:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다 (옵션 미상장 종목이거나 티커 오류일 수 있음)")

    return all_results


def fetch_option_chain(ticker: str, expiry: Optional[str] = None, max_expiries: int = 4) -> list[ChainSnapshot]:
    """
    가까운 만기(들)의 옵션체인을 Massive.com에서 가져온다.
    expiry가 주어지면 그 만기 하나만, 아니면 가장 가까운 max_expiries개를 합쳐서
    (GEX 표준 관례: 근접 만기 여러 개를 합산) 반환한다.
    """
    raw_results = _fetch_full_options_chain(ticker)

    spot = None
    by_expiry: dict[str, dict[float, dict]] = {}

    for item in raw_results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")  # "call" | "put"
        if not exp or strike is None or contract_type not in ("call", "put"):
            continue

        if spot is None:
            underlying = item.get("underlying_asset", {})
            spot = underlying.get("price")

        greeks = item.get("greeks") or {}
        gamma = greeks.get("gamma") or 0.0
        oi = item.get("open_interest") or 0.0

        strikes_dict = by_expiry.setdefault(exp, {})
        row = strikes_dict.setdefault(
            strike, {"call_oi": 0.0, "put_oi": 0.0, "call_gamma": 0.0, "put_gamma": 0.0}
        )
        if contract_type == "call":
            row["call_oi"] = float(oi)
            row["call_gamma"] = float(gamma)
        else:
            row["put_oi"] = float(oi)
            row["put_gamma"] = float(gamma)

    if spot is None:
        raise ValueError(f"{ticker}: 현재가를 가져오지 못했습니다")

    all_expiries = sorted(by_expiry.keys())
    if not all_expiries:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다")

    expiries = [expiry] if expiry else all_expiries[:max_expiries]

    snapshots = []
    for exp in expiries:
        strikes_dict = by_expiry.get(exp, {})
        rows = [
            OptionRow(
                strike=k,
                call_oi=v["call_oi"],
                put_oi=v["put_oi"],
                call_gamma=v["call_gamma"],
                put_gamma=v["put_gamma"],
            )
            for k, v in sorted(strikes_dict.items())
        ]
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
    """
    Massive API가 계약별 실측 gamma를 직접 제공하므로 여기서는 그 값을
    OI/스팟가격과 결합해 달러 기준 GEX로 환산하기만 하면 된다
    (예전 야후 버전처럼 Black-Scholes로 gamma를 역산할 필요 없음).
    """
    gex_by_strike = {}

    for r in rows:
        call_gamma = r.call_gamma
        put_gamma = r.put_gamma

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
    # OI는 만기별로 합산하고, gamma는 만기마다 값이 달라서 근접 만기(첫 snapshot) 값을 우선 사용
    merged: dict[float, OptionRow] = {}
    for snap in snapshots:
        for r in snap.rows:
            if r.strike not in merged:
                merged[r.strike] = OptionRow(r.strike, 0, 0, 0, 0)
            m = merged[r.strike]
            m.call_oi += r.call_oi
            m.put_oi += r.put_oi
            if m.call_gamma == 0:
                m.call_gamma = r.call_gamma
            if m.put_gamma == 0:
                m.put_gamma = r.put_gamma

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
