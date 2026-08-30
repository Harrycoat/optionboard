"""
options_engine.py
------------------
Max Pain + Gamma Exposure(GEX) 계산 엔진.
Massive.com(구 Polygon.io) 유료 옵션체인 API 사용 (Options Starter 플랜, 15분 지연).

필요 환경변수:
  MASSIVE_API_KEY  (Vercel Environment Variables에 등록)

[2026-08-13 수정 내역]
  - 현재가(spot) 조회 순서를 바꿈. 기존에는 옵션체인 응답 안의
    underlying_asset.price를 먼저 믿고 쓰다가, 이 필드가 자주 비어있어서
    뒤늦게 /v2/aggs/ticker/{ticker}/prev(전일 종가) 로 폴백했음. 이 과정에서
    "가격 데이터 주의" 경고가 매번 뜨는 문제가 있었음.
    generate_leaders_report.py 쪽(fetch_daily_bar_once)은 애초에 이
    /v2/aggs/.../prev 엔드포인트를 1차로 확실하게 호출해서 문제가 없었음.
    → 이제 options_engine도 동일하게 /v2/aggs/.../prev를 1차 소스로 삼고,
      옵션체인의 underlying_asset.price는 (있으면) 보조로만 참고한다.
    → 대부분의 경우 prev 호출이 정상 성공하므로 is_stale_price는 이제
      "정말로 아무 가격도 못 가져온 예외적인 경우"에만 True가 된다.
  - [2026-08-12 수정 내역 — 참고용, 아래는 이전 로직에 대한 원인 파악 로그였음]
    근본 원인(underlying_asset.price가 왜 자주 비어있는지)은 Massive API
    응답 스키마 확인이 더 필요함 — 유료 스냅샷/실시간 엔드포인트 사용 여부는
    비용 문제로 보류. [DEBUG spot=None] 로그는 더 이상 필요 없어져서 제거함.
"""

from __future__ import annotations
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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
    call_iv: float = 0.0
    put_iv: float = 0.0


@dataclass
class ChainSnapshot:
    ticker: str
    spot: float
    expiry: str
    rows: list[OptionRow] = field(default_factory=list)
    is_stale_price: bool = False  # True면 prev_close/옵션체인 둘 다에서 정상 가격을 못 가져온 예외 상황
    prev_open: Optional[float] = None  # 등락률(%) 계산용 (전일 시가) — 없으면 None


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
        next_url = next_url + ("&" if "?" in next_url else "?") + f"apiKey={MASSIVE_API_KEY}"

    if not all_results:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다 (옵션 미상장 종목이거나 티커 오류일 수 있음)")

    return all_results


def _fetch_prev_bar(ticker: str) -> Optional[dict]:
    """전일(가장 최근 완결된 거래일) 일봉의 시가/종가/거래량을 반환한다."""
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/prev"
    try:
        data = _massive_get(url)
    except MassiveAPIError:
        return None
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    o, c = r.get("o"), r.get("c")
    if c is None:
        return None
    return {"open": o, "close": float(c), "volume": r.get("v")}


def _resolve_spot(ticker: str, chain_price: Optional[float]) -> tuple[float, bool, Optional[float]]:
    """현재가(spot)를 결정한다.

    1차: /v2/aggs/.../prev (전일 종가) — leaders report와 동일하게 확실히 성공하는 소스.
    2차(보조): 옵션체인 응답에 이미 포함된 underlying_asset.price가 있고
               1차가 실패했을 때만 사용.

    반환: (spot, is_stale_price, prev_open). is_stale_price는 두 소스 다 실패해서
    정말로 아무 가격도 못 가져온 예외적인 경우에만 True. prev_open은 등락률(%)
    계산용 — 1차 소스가 실패했으면 None.
    """
    bar = _fetch_prev_bar(ticker)
    if bar is not None:
        return bar["close"], False, bar.get("open")

    if chain_price is not None:
        return float(chain_price), False, None

    return None, True, None  # 호출부에서 None 체크로 예외 처리


def fetch_oi_volume_snapshot(ticker: str, max_expiries: int = 2) -> dict:
    """OI 변화 추적(롤오버 감지)용 경량 스냅샷.

    스트라이크별 call_oi/put_oi에 더해 당일 거래량(day.volume)까지 함께 담는다.
    거래량은 fetch_option_chain()이 쓰는 GEX용 데이터에는 없어서 별도로 추출한다.
    가까운 만기 max_expiries개를 합산한 값을 기준으로 한다.
    """
    ticker = ticker.upper().strip()
    raw_results = _fetch_full_options_chain(ticker)

    chain_price = None
    by_expiry: dict[str, dict[float, dict]] = {}

    for item in raw_results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if not exp or strike is None or contract_type not in ("call", "put"):
            continue

        if chain_price is None:
            underlying = item.get("underlying_asset", {})
            chain_price = underlying.get("price")

        oi = item.get("open_interest") or 0.0
        day = item.get("day") or {}
        volume = day.get("volume") or 0.0

        strikes_dict = by_expiry.setdefault(exp, {})
        row = strikes_dict.setdefault(
            strike, {"call_oi": 0.0, "put_oi": 0.0, "call_volume": 0.0, "put_volume": 0.0}
        )
        if contract_type == "call":
            row["call_oi"] = float(oi)
            row["call_volume"] = float(volume)
        else:
            row["put_oi"] = float(oi)
            row["put_volume"] = float(volume)

    spot, is_stale_price, _prev_open = _resolve_spot(ticker, chain_price)

    all_expiries = sorted(by_expiry.keys())
    use_expiries = all_expiries[:max_expiries]

    merged: dict[float, dict] = {}
    for exp in use_expiries:
        for strike, row in by_expiry.get(exp, {}).items():
            m = merged.setdefault(strike, {"call_oi": 0.0, "put_oi": 0.0, "call_volume": 0.0, "put_volume": 0.0})
            m["call_oi"] += row["call_oi"]
            m["put_oi"] += row["put_oi"]
            m["call_volume"] += row["call_volume"]
            m["put_volume"] += row["put_volume"]

    return {
        "ticker": ticker,
        "date": date.today().isoformat(),
        "spot": spot,
        "expiries_used": use_expiries,
        "strikes": [
            {"strike": k, **v} for k, v in sorted(merged.items())
        ],
    }


def compute_oi_rollover(prev_snapshot: dict, today_snapshot: dict, min_oi_change: float = 500) -> list[dict]:
    """전일 스냅샷과 오늘 스냅샷을 스트라이크별로 비교해서 신규/청산 자리를 찾는다.

    거래량이 OI 변화량과 비슷하게 맞아떨어지는 경우("실제로 그날 그 물량이 손바뀜했다")를
    우선적으로 신뢰도 높은 신호로 표시한다.
    """
    if not prev_snapshot or not today_snapshot:
        return []

    prev_map = {(s["strike"], side): s for s in prev_snapshot.get("strikes", []) for side in ("call", "put")}
    today_map = {(s["strike"], side): s for s in today_snapshot.get("strikes", []) for side in ("call", "put")}

    all_keys = set(prev_map.keys()) | set(today_map.keys())
    results = []

    for strike, side in all_keys:
        prev_row = prev_map.get((strike, side), {})
        today_row = today_map.get((strike, side), {})
        prev_oi = prev_row.get(f"{side}_oi", 0.0)
        today_oi = today_row.get(f"{side}_oi", 0.0)
        today_volume = today_row.get(f"{side}_volume", 0.0)

        oi_change = today_oi - prev_oi
        if abs(oi_change) < min_oi_change:
            continue

        # 거래량이 OI 변화량의 절반 이상이면 "실제 체결로 확인된" 신호로 간주
        volume_confirmed = today_volume >= abs(oi_change) * 0.5

        if prev_oi < min_oi_change * 0.2 and today_oi >= min_oi_change:
            classification = "신규생성"
        elif today_oi < prev_oi * 0.15 and prev_oi >= min_oi_change:
            classification = "청산"
        elif oi_change > 0:
            classification = "증가"
        else:
            classification = "감소"

        results.append({
            "strike": strike,
            "side": side,
            "prev_oi": round(prev_oi),
            "today_oi": round(today_oi),
            "oi_change": round(oi_change),
            "today_volume": round(today_volume),
            "volume_confirmed": volume_confirmed,
            "classification": classification,
        })

    results.sort(key=lambda r: abs(r["oi_change"]), reverse=True)
    return results


def fetch_option_chain(ticker: str, expiry: Optional[str] = None, max_expiries: int = 4) -> list[ChainSnapshot]:
    raw_results = _fetch_full_options_chain(ticker)

    chain_price = None
    by_expiry: dict[str, dict[float, dict]] = {}

    for item in raw_results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if not exp or strike is None or contract_type not in ("call", "put"):
            continue

        if chain_price is None:
            underlying = item.get("underlying_asset", {})
            chain_price = underlying.get("price")

        greeks = item.get("greeks") or {}
        gamma = greeks.get("gamma") or 0.0
        oi = item.get("open_interest") or 0.0
        iv = item.get("implied_volatility") or 0.0

        strikes_dict = by_expiry.setdefault(exp, {})
        row = strikes_dict.setdefault(
            strike, {"call_oi": 0.0, "put_oi": 0.0, "call_gamma": 0.0, "put_gamma": 0.0, "call_iv": 0.0, "put_iv": 0.0}
        )
        if contract_type == "call":
            row["call_oi"] = float(oi)
            row["call_gamma"] = float(gamma)
            row["call_iv"] = float(iv)
        else:
            row["put_oi"] = float(oi)
            row["put_gamma"] = float(gamma)
            row["put_iv"] = float(iv)

    spot, is_stale_price, prev_open = _resolve_spot(ticker, chain_price)

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
                call_iv=v.get("call_iv", 0.0),
                put_iv=v.get("put_iv", 0.0),
            )
            for k, v in sorted(strikes_dict.items())
        ]
        snapshots.append(
            ChainSnapshot(
                ticker=ticker,
                spot=float(spot),
                expiry=exp,
                rows=rows,
                is_stale_price=is_stale_price,
                prev_open=prev_open,
            )
        )

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
        pain_by_strike[candidate] = (call_payout + put_payout) * 100

    max_pain_strike = min(pain_by_strike, key=pain_by_strike.get)
    return {
        "max_pain_strike": max_pain_strike,
        "pain_curve": [{"strike": k, "total_pain": v} for k, v in sorted(pain_by_strike.items())],
    }


def compute_max_oi_wall(rows: list[OptionRow]) -> dict:
    """감마 가중치와 무관하게, 콜+풋 순수 미결제약정(OI) 합산이 가장 큰 스트라이크.

    Call Wall(감마 기준)과는 별개의 개념 — 이미 있는 call_oi/put_oi로 바로 계산 가능하며
    추가 API 호출이 필요 없다.
    """
    if not rows:
        return {"max_oi_wall": None, "max_oi_wall_total_oi": 0, "max_oi_wall_call_oi": 0, "max_oi_wall_put_oi": 0}

    total_oi_by_strike = {r.strike: (r.call_oi + r.put_oi) for r in rows}
    best_strike = max(total_oi_by_strike, key=total_oi_by_strike.get)
    best_row = next(r for r in rows if r.strike == best_strike)

    return {
        "max_oi_wall": best_strike,
        "max_oi_wall_total_oi": total_oi_by_strike[best_strike],
        "max_oi_wall_call_oi": best_row.call_oi,
        "max_oi_wall_put_oi": best_row.put_oi,
    }


# ---------------------------------------------------------------------------
# GEX (Gamma Exposure)
# ---------------------------------------------------------------------------
def compute_gex(spot: float, expiry: str, rows: list[OptionRow]) -> dict:
    gex_by_strike = {}

    for r in rows:
        call_gamma = r.call_gamma
        put_gamma = r.put_gamma

        call_gex = call_gamma * r.call_oi * 100 * (spot ** 2) * 0.01
        put_gex = -1 * put_gamma * r.put_oi * 100 * (spot ** 2) * 0.01

        gex_by_strike[r.strike] = {
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": call_gex + put_gex,
        }

    return _summarize_gex_by_strike(spot, gex_by_strike)


def compute_gex_multi_expiry(spot: float, snapshots: list["ChainSnapshot"]) -> dict:
    """여러 만기(snapshot)의 GEX를 올바르게 합산한다.

    수정 이력: 예전에는 analyze_ticker()에서 만기별 OptionRow를 먼저
    "병합"(OI는 더하고 감마는 처음 만난 값만 사용)한 뒤 compute_gex()에
    넘겼다. 그런데 감마는 만기마다 다른 값인데 그중 하나만 갖다 쓰고
    OI는 전체를 더해버리면, 실제로는 다른 만기에 몰려있는 큰 물량이
    엉뚱한 감마값으로 계산돼서 Call Wall/Put Wall이 틀어지는 문제가 있었다
    (예: BE의 실제 Call Wall은 250인데 210으로 잘못 계산됨).

    올바른 방법: 만기별로 각자의 감마+OI로 GEX(달러 노출)를 먼저 계산하고,
    그 "이미 계산 완료된 GEX 금액"을 스트라이크별로 합산한다. 원본 감마
    수치 자체는 만기가 다르면 그냥 더할 수 없지만, GEX 금액(감마×OI×...)은
    둘 다 달러 단위라 만기가 달라도 합산 가능하다.
    """
    combined: dict[float, dict] = {}
    for snap in snapshots:
        for r in snap.rows:
            call_gex = r.call_gamma * r.call_oi * 100 * (spot ** 2) * 0.01
            put_gex = -1 * r.put_gamma * r.put_oi * 100 * (spot ** 2) * 0.01
            entry = combined.setdefault(r.strike, {"call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0})
            entry["call_gex"] += call_gex
            entry["put_gex"] += put_gex
            entry["net_gex"] += call_gex + put_gex

    return _summarize_gex_by_strike(spot, combined)


def _summarize_gex_by_strike(spot: float, gex_by_strike: dict[float, dict]) -> dict:
    """이미 계산된 스트라이크별 GEX 딕셔너리에서 Call Wall/Put Wall/Gamma Flip
    (cumulative 방식, 폴백용)/Net GEX/체제를 뽑아낸다.

    compute_gex()와 compute_gex_multi_expiry() 둘 다 이 함수를 공유한다.
    """
    if not gex_by_strike:
        return {"call_wall": None, "put_wall": None, "gamma_flip": None, "net_gex_total": 0, "by_strike": []}

    strikes_at_or_above = {k: v for k, v in gex_by_strike.items() if k >= spot}
    strikes_at_or_below = {k: v for k, v in gex_by_strike.items() if k <= spot}

    call_candidates = strikes_at_or_above if strikes_at_or_above else gex_by_strike
    put_candidates = strikes_at_or_below if strikes_at_or_below else gex_by_strike

    call_wall = max(call_candidates, key=lambda k: call_candidates[k]["call_gex"])
    put_wall = min(put_candidates, key=lambda k: put_candidates[k]["put_gex"])

    # ------------------------------------------------------------------
    # Gamma Flip 계산(cumulative crossing 방식) — 지금은 compute_gamma_flip_bs()의
    # 블랙숄즈 재계산 방식이 우선 사용되고, 이건 그게 실패했을 때만 쓰는 폴백이다.
    # 스트라이크를 오름차순으로 훑으면서 누적 net_gex의 부호가 바뀌는 지점(들)을
    # 전부 찾은 뒤, 그중 "스팟 가격에 가장 가까운" 지점을 고른다.
    # ------------------------------------------------------------------
    sorted_strikes = sorted(gex_by_strike.keys())
    cumulative = 0.0
    prev_strike = None
    crossings: list[float] = []
    for k in sorted_strikes:
        prev_cum = cumulative
        cumulative += gex_by_strike[k]["net_gex"]
        if prev_strike is not None and prev_cum < 0 <= cumulative:
            crossings.append(k)
        prev_strike = k

    if crossings:
        gamma_flip = min(crossings, key=lambda k: abs(k - spot))
    elif sorted_strikes:
        gamma_flip = min(sorted_strikes, key=lambda k: abs(gex_by_strike[k]["net_gex"]))
    else:
        gamma_flip = None

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
# Vanna / Charm Exposure (2차 그릭스, 딜러 헤지 흐름)
# Black-Scholes 순정 파이썬 구현 (scipy 등 무거운 의존성 없음).
# scipy 없이 표준정규분포 확률밀도함수(PDF)만 직접 구현해서 사용한다.
#
# Vanna: 변동성(IV) 변화에 따른 딜러 델타 헤지 흐름.
#   콜/풋 동일 부호 → 종목별로 콜+풋 OI를 더해서(add) 노출도 계산.
# Charm: 시간(잔존만기) 경과에 따른 딜러 델타 헤지 흐름 (특히 만기 임박 시 강함).
#   콜/풋 반대 부호 → GEX처럼 콜-풋 OI 차이로 계산.
#
# 참고: 근월물(가장 가까운 만기) 1개 스냅샷 기준으로만 계산한다.
# (여러 만기를 합치면 만기별로 다른 잔존기간 T를 반영할 수 없어서 부정확해짐)
# ---------------------------------------------------------------------------
RISK_FREE_RATE_DEFAULT = 0.045  # 무위험 이자율 근사치 (미 단기 국채 수준)


def _norm_pdf(x: float) -> float:
    """표준정규분포 확률밀도함수 φ(x). scipy 없이 math만으로 구현."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1_d2(spot: float, strike: float, t_years: float, r: float, iv: float) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    return d1, d2


def _bs_vanna(spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """콜/풋 공통 (put-call parity에 의해 동일값). 계약 1개(주식 1주) 기준."""
    d1, d2 = _bs_d1_d2(spot, strike, t_years, r, iv)
    return -math.exp(-r * t_years) * _norm_pdf(d1) * d2 / iv


def _bs_charm_call(spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """콜옵션 기준 charm. 풋은 부호 반대(-charm_call)."""
    d1, d2 = _bs_d1_d2(spot, strike, t_years, r, iv)
    sqrt_t = math.sqrt(t_years)
    return -_norm_pdf(d1) * (2 * r * t_years - d2 * iv * sqrt_t) / (2 * t_years * iv * sqrt_t)


def _bs_gamma(spot: float, strike: float, t_years: float, r: float, iv: float) -> float:
    """블랙숄즈 감마 (콜/풋 공통값)."""
    d1, _ = _bs_d1_d2(spot, strike, t_years, r, iv)
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def compute_gamma_flip_bs(
    spot: float,
    rows: list[OptionRow],
    expiry: str,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> Optional[float]:
    """"진짜" 감마 플립 계산.

    수정 이력: 예전 방식(compute_gex 안의 cumulative crossing)은 각 스트라이크의
    OI×감마(현재 스팟 기준으로 계산된 값)를 낮은 스트라이크부터 그냥 누적 합산해서
    처음 만나는 부호전환 지점을 찾았다. 이건 "스팟이 그 스트라이크에 있었다면
    감마가 어땠을지"를 반영하지 못하는 방식이라, 스팟에서 한참 먼 스트라이크의
    소량 잔여 OI 때문에 엉뚱한 값이 나오는 경우가 있었다 (예: SNDK 스팟 1596인데
    flip이 615로 나옴).

    이 함수는 실제 업계에서 쓰는 정의대로, 여러 가상의 스팟 가격에서 블랙숄즈
    감마를 다시 계산해서(해당 가격을 스팟으로 가정했을 때의 감마), 딜러 전체
    감마노출의 부호가 실제로 뒤집히는 지점을 찾는다 (Barchart 등에서 "1% move
    기준"이라고 설명하는 방식과 같은 개념).

    근월물(가장 가까운 만기) 1개 스냅샷 기준으로만 계산한다 — compute_vanna_charm과
    동일한 이유로, 만기가 섞이면 잔존기간 T가 달라져서 부정확해진다.
    """
    today = date.today()
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        return None

    days_to_expiry = (exp_date - today).days
    t_years = days_to_expiry / 365.0
    if t_years <= 0:
        return None

    valid_rows = [r for r in rows if (r.call_oi and r.call_iv) or (r.put_oi and r.put_iv)]
    if not valid_rows:
        return None

    def total_gamma_at(test_spot: float) -> float:
        total = 0.0
        for r in valid_rows:
            if r.call_oi and r.call_iv:
                g = _bs_gamma(test_spot, r.strike, t_years, risk_free_rate, r.call_iv)
                total += g * r.call_oi * 100 * test_spot ** 2 * 0.01
            if r.put_oi and r.put_iv:
                g = _bs_gamma(test_spot, r.strike, t_years, risk_free_rate, r.put_iv)
                total -= g * r.put_oi * 100 * test_spot ** 2 * 0.01
        return total

    # 스팟 근처 ±30% 범위를 촘촘히 스캔해서 부호전환 지점을 찾는다.
    # 여러 지점에서 전환이 생길 수 있어 스팟에서 가장 가까운 것을 최종 채택한다.
    lo, hi = spot * 0.7, spot * 1.3
    n_steps = 200
    step = (hi - lo) / n_steps
    prev_price, prev_val = lo, total_gamma_at(lo)
    best_crossing: Optional[float] = None
    for i in range(1, n_steps + 1):
        price = lo + step * i
        val = total_gamma_at(price)
        if (prev_val < 0 <= val) or (prev_val >= 0 > val):
            denom = abs(prev_val) + abs(val)
            frac = abs(prev_val) / denom if denom else 0.0
            crossing_price = prev_price + frac * (price - prev_price)
            if best_crossing is None or abs(crossing_price - spot) < abs(best_crossing - spot):
                best_crossing = crossing_price
        prev_price, prev_val = price, val

    return round(best_crossing, 2) if best_crossing is not None else None


def compute_vanna_charm(
    spot: float,
    expiry: str,
    rows: list[OptionRow],
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> dict:
    """단일 만기 스냅샷 기준 Net Vanna Exposure(VEX) / Net Charm Exposure(CEX) 계산.

    IV(implied_volatility)가 없거나(0) 잔존만기가 0 이하인 행(만기 당일 등)은
    Black-Scholes 공식이 정의되지 않으므로 건너뛴다.
    """
    today = date.today()
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        return {"vex_total": 0.0, "cex_total": 0.0, "days_to_expiry": None, "by_strike": []}

    days_to_expiry = (exp_date - today).days
    t_years = days_to_expiry / 365.0

    by_strike = []
    vex_total = 0.0
    cex_total = 0.0

    if t_years <= 0:
        # 만기 당일/지난 만기는 Black-Scholes 공식 정의역 밖이라 스킵
        return {"vex_total": 0.0, "cex_total": 0.0, "days_to_expiry": days_to_expiry, "by_strike": []}

    for r in rows:
        strike_vex = 0.0
        strike_cex = 0.0

        if r.call_iv and r.call_oi:
            vanna_c = _bs_vanna(spot, r.strike, t_years, risk_free_rate, r.call_iv)
            charm_c = _bs_charm_call(spot, r.strike, t_years, risk_free_rate, r.call_iv)
            strike_vex += r.call_oi * vanna_c * 100 * spot
            strike_cex += r.call_oi * charm_c * 100 * spot

        if r.put_iv and r.put_oi:
            vanna_p = _bs_vanna(spot, r.strike, t_years, risk_free_rate, r.put_iv)
            charm_p = -_bs_charm_call(spot, r.strike, t_years, risk_free_rate, r.put_iv)
            strike_vex += r.put_oi * vanna_p * 100 * spot
            strike_cex += r.put_oi * charm_p * 100 * spot

        if strike_vex or strike_cex:
            by_strike.append({"strike": r.strike, "vex": strike_vex, "cex": strike_cex})
        vex_total += strike_vex
        cex_total += strike_cex

    return {
        "vex_total": vex_total,
        "cex_total": cex_total,
        "days_to_expiry": days_to_expiry,
        "by_strike": by_strike,
    }

# ---------------------------------------------------------------------------
# Stage 분석 (Weinstein 4단계, 간단 버전 — 30주선 기울기 + 가격위치)
# 지금은 원인 파악을 위해 stage_debug 필드를 임시로 노출한다.
# ---------------------------------------------------------------------------
def fetch_daily_closes(ticker: str, lookback_days: int = 420) -> tuple[list[float], str]:
    """
    최근 lookback_days(달력일 기준)치 일봉 종가를 오래된 순으로 반환한다.
    (closes, debug_info) 튜플을 반환한다 — debug_info는 원인 파악용 임시 필드.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
    try:
        data = _massive_get(url, {"adjusted": "true", "sort": "asc", "limit": 500})
    except MassiveAPIError as e:
        return [], f"API 오류: {e}"
    results = data.get("results") or []
    closes = [r.get("c") for r in results if r.get("c") is not None]
    debug = f"start={start.isoformat()} end={end.isoformat()} raw_results={len(results)} closes={len(closes)} status={data.get('status')}"
    return closes, debug


def compute_stage(closes: list[float]) -> dict:
    result = {"stage": None, "label": None, "sma150": None, "slope_pct": None, "n_closes": len(closes)}

    if len(closes) < 190:
        return result

    def sma(vals: list[float]) -> float:
        return sum(vals) / len(vals)

    sma_series = [sma(closes[i - 149:i + 1]) for i in range(149, len(closes))]
    if len(sma_series) < 80:
        return result

    sma_now = sma_series[-1]
    sma_recent = sma_series[-40] if len(sma_series) >= 40 else sma_series[0]
    sma_earlier = sma_series[-80] if len(sma_series) >= 80 else sma_series[0]

    price_now = closes[-1]
    slope_pct = (sma_now - sma_recent) / sma_recent * 100 if sma_recent else 0
    prior_slope_pct = (sma_recent - sma_earlier) / sma_earlier * 100 if sma_earlier else 0
    pos_pct = (price_now - sma_now) / sma_now * 100 if sma_now else 0

    SLOPE_THRESH = 2.0
    POS_THRESH = 2.0

    # ------------------------------------------------------------------
    # 수정 이력: 예전 로직은 pos_pct/slope_pct 둘 다 뚜렷하지 않으면
    # (예: 최근 눌림으로 pos_pct가 애매해진 상태) 곧바로 prior_slope_pct
    # (80~40일 전, 즉 몇 달 전 낡은 구간)로 폴백해서 Stage를 확정해버렸다.
    # 그 결과 "MA는 계속 오르는 중인데 가격이 이미 고점 찍고 눌리는" 전형적인
    # 천정권(Stage 3) 패턴이, 몇 달 전 조정기의 낡은 기울기 때문에
    # 바닥다지기(Stage 1)로 잘못 표시되는 사례가 있었다 (예: MSFT 8월).
    #
    # 고친 로직: prior_slope_pct는 "MA 기울기 자체가 애매한(거의 평평한)"
    # 경우에만 타이브레이커로 쓰고, MA 기울기(slope_pct)가 뚜렷하면
    # 항상 "현재 가격 위치 vs 현재 MA 기울기" 조합을 우선한다.
    #   - MA 오름세(slope_pct 양수)인데 가격이 MA 위에서 못 버티면(pos_pct 낮음)
    #     → 천정권(Stage 3): 추세는 아직 살아있지만 가격이 먼저 꺾인 상태
    #   - MA 내림세(slope_pct 음수)인데 가격이 이미 MA 위로 올라오면(pos_pct 높음)
    #     → 바닥다지기(Stage 1): MA는 아직 안 돌았지만 가격이 먼저 반등한 상태
    # ------------------------------------------------------------------
    if pos_pct > POS_THRESH and slope_pct > SLOPE_THRESH:
        stage, label = 2, "상승국면"
    elif pos_pct < -POS_THRESH and slope_pct < -SLOPE_THRESH:
        stage, label = 4, "하락국면"
    elif slope_pct > SLOPE_THRESH:
        # MA는 여전히 뚜렷하게 오르는 중인데 가격이 그 위에서 못 버팀 → 천정권
        stage, label = 3, "천정권"
    elif slope_pct < -SLOPE_THRESH:
        # MA는 여전히 뚜렷하게 내리는 중인데 가격이 이미 위로 올라옴 → 바닥다지기
        stage, label = 1, "바닥다지기"
    elif pos_pct > POS_THRESH:
        # MA 기울기는 애매(평평)하지만, 가격은 여전히 뚜렷하게 MA 위 → 아직 상승 흐름 유지로 판단
        # (낡은 prior_slope_pct보다 "지금 가격이 MA 위에 있다"는 현재 신호를 우선)
        stage, label = 2, "상승국면"
    elif pos_pct < -POS_THRESH:
        # 반대로 가격이 여전히 뚜렷하게 MA 아래 → 아직 하락 흐름 유지로 판단
        stage, label = 4, "하락국면"
    elif prior_slope_pct < -SLOPE_THRESH:
        # MA 기울기·가격위치 둘 다 애매(평평)할 때만 낡은 흐름을 타이브레이커로 사용
        stage, label = 1, "바닥다지기"
    elif prior_slope_pct > SLOPE_THRESH:
        stage, label = 3, "천정권"
    else:
        stage, label = (2, "상승국면") if pos_pct >= 0 else (4, "하락국면")

    result.update({
        "stage": stage,
        "label": label,
        "sma150": round(sma_now, 2),
        "slope_pct": round(slope_pct, 2),
        "pos_pct": round(pos_pct, 2),
        "prior_slope_pct": round(prior_slope_pct, 2),
    })
    return result


# ---------------------------------------------------------------------------
# 한국어 해설 문장 생성 (참고용 — 매수/매도 지시 아님)
# ---------------------------------------------------------------------------
def _fmt_strike(x) -> str:
    if x is None:
        return "-"
    x = float(x)
    return f"{x:.0f}" if x.is_integer() else f"{x:.1f}"


def build_narrative(
    spot: float,
    max_pain: Optional[float],
    call_wall: Optional[float],
    put_wall: Optional[float],
    gamma_flip: Optional[float],
    regime: str,
    expiry_used: str,
    is_stale_price: bool = False,
) -> list[str]:
    lines: list[str] = []

    if is_stale_price:
        lines.append(
            "<b>⚠️ 가격 데이터 주의</b>: 이번 스캔은 현재가를 정상적으로 가져오지 못해 "
            "계산이 부정확할 수 있어요. 잠시 후 다시 조회하거나, 실전 매매 전 반드시 "
            "실시간 시세로 재확인하세요."
        )

    if regime == "positive":
        lines.append(
            "<b>양의 감마 (변동성 둔화)</b>: 딜러들이 가격을 누르고 받쳐주는 방향으로 헤지하는 경향이 있어, "
            "큰 폭의 급등락보다는 상대적으로 좁은 박스권 흐름이 나올 가능성이 있어요."
        )
    else:
        lines.append(
            "<b>음의 감마 (변동성 확대)</b>: 딜러들이 가격 움직임을 증폭시키는 방향으로 헤지하는 경향이 있어, "
            "추세가 붙으면 변동성이 커질 가능성이 있어요."
        )

    key_lines: list[str] = []
    if put_wall is not None and spot:
        put_pct = (put_wall - spot) / spot * 100
        key_lines.append(
            f"하방 지지 (Put Wall): {_fmt_strike(put_wall)} ({put_pct:+.1f}%) — 이 근처에서 반등할지 체크"
        )
    if call_wall is not None and spot:
        call_pct = (call_wall - spot) / spot * 100
        key_lines.append(
            f"상방 저항 (Call Wall): {_fmt_strike(call_wall)} ({call_pct:+.1f}%) — 이 근처에서 막히는지 체크"
        )
    if gamma_flip is not None and spot:
        direction = "위" if spot >= gamma_flip else "아래"
        key_lines.append(
            f"감마 플립: {_fmt_strike(gamma_flip)} — 추세 변화의 기점이 되는 위치 (현재는 {direction}에 위치)"
        )

    if key_lines:
        lines.append("<b>주요 핵심 라인:</b>")
        for kl in key_lines:
            lines.append(f"&nbsp;&nbsp;• {kl}")

    if max_pain is not None and spot:
        day_text = ""
        try:
            exp_date = datetime.strptime(expiry_used, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_left = max((exp_date - datetime.now(timezone.utc)).days, 0)
            day_text = f", D-{days_left}"
        except (ValueError, TypeError):
            pass
        pain_pct = (max_pain - spot) / spot * 100
        lines.append(
            f"<b>만기일 ({expiry_used}{day_text}) 전망</b>: 'Max Pain 이론'에 따르면 만기가 다가올수록 주가가 "
            f"{_fmt_strike(max_pain)} 근처로 수렴하는 경향이 있다는 가설이 있어요 (현재가 대비 {pain_pct:+.1f}%). "
            "다만 이는 하나의 참고 가설일 뿐, 실적 발표나 거시경제 이벤트 등 다른 요인이 훨씬 크게 작용할 수 있어요."
        )

    if put_wall is not None and call_wall is not None and spot:
        target = max_pain if max_pain is not None else call_wall
        target_label = "Max Pain" if max_pain is not None else "Call Wall"
        pull_word = "끌려 올라갈" if target >= spot else "끌려 내려갈"
        lines.append(
            f"💡 <b>한 줄 정리</b>: \"딜러들의 헤지 물량 때문에 {_fmt_strike(put_wall)}~{_fmt_strike(call_wall)} "
            f"사이 박스권에서 움직이되, 옵션 만기 특성상 {_fmt_strike(target)}({target_label}) 쪽으로 수렴하며 "
            f"{pull_word} 가능성이 있는 상태\""
        )

    return lines

# ---------------------------------------------------------------------------
# 통합 분석
# ---------------------------------------------------------------------------
def analyze_ticker(ticker: str, expiry: Optional[str] = None) -> dict:
    ticker = ticker.upper().strip()
    snapshots = fetch_option_chain(ticker, expiry=expiry)

    primary = snapshots[0]
    max_pain = compute_max_pain(primary.rows)

    price_change_pct = None
    if primary.prev_open:
        price_change_pct = round((primary.spot - primary.prev_open) / primary.prev_open * 100, 2)

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

    oi_by_strike = [
        {"strike": k, "call_oi": m.call_oi, "put_oi": m.put_oi}
        for k, m in sorted(merged.items())
    ]
    gex = compute_gex_multi_expiry(primary.spot, snapshots)
    max_oi_wall = compute_max_oi_wall(list(merged.values()))
    vanna_charm = compute_vanna_charm(primary.spot, primary.expiry, primary.rows)

    # 진짜(블랙숄즈 재계산 방식) 감마 플립. IV 데이터가 부족해서 계산이
    # 안 되는 예외적인 경우에만 예전 방식(cumulative crossing) 값으로 폴백한다.
    gamma_flip_bs = compute_gamma_flip_bs(primary.spot, primary.rows, primary.expiry)
    gamma_flip = gamma_flip_bs if gamma_flip_bs is not None else gex["gamma_flip"]

    narrative = build_narrative(
        spot=primary.spot,
        max_pain=max_pain["max_pain_strike"],
        call_wall=gex["call_wall"],
        put_wall=gex["put_wall"],
        gamma_flip=gamma_flip,
        regime=gex["regime"],
        expiry_used=primary.expiry,
        is_stale_price=primary.is_stale_price,
    )

    stage_debug = None
    try:
        closes, stage_debug = fetch_daily_closes(ticker)
        stage_info = compute_stage(closes)
    except Exception as e:
        stage_info = {"stage": None, "label": None, "sma150": None, "slope_pct": None, "pos_pct": None, "prior_slope_pct": None, "n_closes": 0}
        stage_debug = f"예외 발생: {e}"

    return {
        "ticker": ticker,
        "spot": primary.spot,
        "is_stale_price": primary.is_stale_price,
        "price_change_pct": price_change_pct,
        "expiry_used": primary.expiry,
        "expiries_included_in_gex": [s.expiry for s in snapshots],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_pain": max_pain["max_pain_strike"],
        "pain_curve": max_pain["pain_curve"],
        "call_wall": gex["call_wall"],
        "put_wall": gex["put_wall"],
        "gamma_flip": gamma_flip,
        "max_oi_wall": max_oi_wall["max_oi_wall"],
        "max_oi_wall_total_oi": max_oi_wall["max_oi_wall_total_oi"],
        "max_oi_wall_call_oi": max_oi_wall["max_oi_wall_call_oi"],
        "max_oi_wall_put_oi": max_oi_wall["max_oi_wall_put_oi"],
        "net_gex_total": gex["net_gex_total"],
        "regime": gex["regime"],
        "gex_by_strike": gex["by_strike"],
        "oi_by_strike": oi_by_strike,
        "vex_total": vanna_charm["vex_total"],
        "cex_total": vanna_charm["cex_total"],
        "vanna_charm_expiry_days": vanna_charm["days_to_expiry"],
        "narrative": narrative,
        "stage": stage_info["stage"],
        "stage_label": stage_info["label"],
        "stage_sma150": stage_info["sma150"],
        "stage_slope_pct": stage_info["slope_pct"],
        "stage_pos_pct": stage_info.get("pos_pct"),
        "stage_prior_slope_pct": stage_info.get("prior_slope_pct"),
        "stage_debug": stage_debug,
        "stage_n_closes": stage_info.get("n_closes"),
    }


# ---------------------------------------------------------------------------
# 짧은 캐싱 레이어 (트래픽 급증 시 Massive API 요청량 방어용)
#
# Vercel 서버리스 함수는 인스턴스가 재사용될 때만 이 캐시가 유지된다
# (완전히 새 인스턴스가 뜨면 캐시는 비어있는 상태로 시작함 — 이건 정상이고,
#  그래도 같은 인스턴스가 짧은 시간 안에 여러 요청을 처리하는 경우
#  — 예: 동시에 여러 명이 같은 종목 검색, 매크로/주도주 보드가
#  겹치는 티커를 반복 조회하는 경우 — 에는 Massive API 호출을 줄여준다.
#
# 데이터 자체가 원래 "15분 지연"이라, 60~90초 정도 캐싱해도
# 정확도 손실이 사실상 없다.
# ---------------------------------------------------------------------------
CACHE_TTL_SECONDS = 90
_analysis_cache: dict[tuple, tuple[float, dict]] = {}


def analyze_ticker_cached(ticker: str, expiry: Optional[str] = None, ttl: int = CACHE_TTL_SECONDS) -> dict:
    """analyze_ticker()에 짧은 TTL 캐싱을 씌운 버전.

    같은 (ticker, expiry) 조합이 ttl초 이내에 다시 요청되면 Massive API를
    다시 호출하지 않고 캐시된 결과를 그대로 반환한다.
    """
    key = (ticker.upper().strip(), expiry)
    now = time.time()

    cached = _analysis_cache.get(key)
    if cached is not None:
        cached_at, cached_result = cached
        if now - cached_at < ttl:
            return cached_result

    result = analyze_ticker(ticker, expiry=expiry)
    _analysis_cache[key] = (now, result)
    return result


# ---------------------------------------------------------------------------
# Top10 Gamma Flip 스캐너 전용 초경량 분석
# ---------------------------------------------------------------------------
def quick_gamma_flip(ticker: str) -> dict:
    """Top10 스캐너 전용 초경량 분석.

    API 요청 자체에 expiration_date 필터를 걸어서 근월물 계약만 딱 1페이지
    (최대 250개) 받아온다. 현재가는 /v2/aggs/.../prev를 1차로 사용하고,
    옵션체인의 underlying_asset.price는 (있으면) 보조로만 참고한다.
    """
    ticker = ticker.upper().strip()
    today = date.today().isoformat()

    url = f"{MASSIVE_API_BASE}/v3/snapshot/options/{ticker}"
    params = {
        "expiration_date.gte": today,
        "limit": 250,
        "sort": "expiration_date",
        "order": "asc",
    }
    data = _massive_get(url, params)
    results = data.get("results", [])

    if not results:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다")

    chain_price = None
    strikes_dict: dict[float, dict] = {}
    nearest_expiry = None

    for item in results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if not exp or strike is None or contract_type not in ("call", "put"):
            continue

        # 첫 번째로 만나는(=가장 가까운, asc 정렬) 만기만 사용
        if nearest_expiry is None:
            nearest_expiry = exp
        if exp != nearest_expiry:
            continue

        if chain_price is None:
            underlying = item.get("underlying_asset", {})
            chain_price = underlying.get("price")

        greeks = item.get("greeks") or {}
        gamma = greeks.get("gamma") or 0.0
        oi = item.get("open_interest") or 0.0

        row = strikes_dict.setdefault(
            strike, {"call_oi": 0.0, "put_oi": 0.0, "call_gamma": 0.0, "put_gamma": 0.0}
        )
        if contract_type == "call":
            row["call_oi"] = float(oi)
            row["call_gamma"] = float(gamma)
        else:
            row["put_oi"] = float(oi)
            row["put_gamma"] = float(gamma)

    spot, is_stale_price, _prev_open = _resolve_spot(ticker, chain_price)

    if spot is None or not strikes_dict:
        raise ValueError(f"{ticker}: 현재가 또는 옵션 데이터를 가져오지 못했습니다")

    rows = [
        OptionRow(
            strike=k,
            call_oi=v["call_oi"],
            put_oi=v["put_oi"],
            call_gamma=v["call_gamma"],
            put_gamma=v["put_gamma"],
        )
        for k, v in strikes_dict.items()
    ]

    gex = compute_gex(float(spot), nearest_expiry, rows)

    return {
        "ticker": ticker,
        "spot": float(spot),
        "is_stale_price": is_stale_price,
        "expiry_used": nearest_expiry,
        "gamma_flip": gex["gamma_flip"],
        "regime": gex["regime"],
        "net_gex_total": gex["net_gex_total"],
    }


def rank_by_liquidity(ticker: str) -> dict:
    """유동성 랭킹 전용 초경량 조회."""
    ticker = ticker.upper().strip()
    today = date.today().isoformat()
    url = f"{MASSIVE_API_BASE}/v3/snapshot/options/{ticker}"
    params = {"expiration_date.gte": today, "limit": 250, "sort": "expiration_date", "order": "asc"}
    data = _massive_get(url, params)
    results = data.get("results", [])
    if not results:
        raise ValueError(f"{ticker}: 옵션체인이 없습니다")
    nearest_expiry = None
    total_oi = 0.0
    for item in results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        if not exp:
            continue
        if nearest_expiry is None:
            nearest_expiry = exp
        if exp != nearest_expiry:
            continue
        oi = item.get("open_interest") or 0.0
        total_oi += float(oi)
    return {"ticker": ticker, "total_oi": total_oi}


def fetch_daily_ohlc(ticker: str, lookback_days: int = 180) -> tuple[list[dict], str]:
    """캔들차트용 일봉 OHLC(+거래량)를 오래된 순으로 반환한다.

    fetch_daily_closes()와 달리 종가뿐 아니라 시가/고가/저가/거래량까지 반환한다.
    (bars, debug_info) 튜플을 반환한다 — debug_info는 원인 파악용 임시 필드.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
    try:
        data = _massive_get(url, {"adjusted": "true", "sort": "asc", "limit": 500})
    except MassiveAPIError as e:
        return [], f"API 오류: {e}"
    results = data.get("results") or []

    bars = []
    skipped = 0
    for r in results:
        ts = r.get("t")
        o, h, l, c, v = r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v")
        if None in (ts, o, h, l, c):
            skipped += 1
            continue
        day_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        bars.append({
            "time": day_str,
            "open": round(float(o), 4),
            "high": round(float(h), 4),
            "low": round(float(l), 4),
            "close": round(float(c), 4),
            "volume": v,
        })

    debug = f"start={start.isoformat()} end={end.isoformat()} raw_results={len(results)} bars={len(bars)} skipped={skipped} status={data.get('status')}"
    return bars, debug


if __name__ == "__main__":
    import json
    import sys

    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    result = analyze_ticker(t)
    print(json.dumps({k: v for k, v in result.items() if k not in ("pain_curve", "gex_by_strike")}, indent=2))
