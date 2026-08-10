"""
options_engine.py
------------------
Max Pain + Gamma Exposure(GEX) 계산 엔진.
Massive.com(구 Polygon.io) 유료 옵션체인 API 사용 (Options Starter 플랜, 15분 지연).

필요 환경변수:
  MASSIVE_API_KEY  (Vercel Environment Variables에 등록)
"""

from __future__ import annotations
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


def _fetch_prev_close(ticker: str) -> Optional[float]:
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/prev"
    try:
        data = _massive_get(url)
    except MassiveAPIError:
        return None
    results = data.get("results") or []
    if not results:
        return None
    close = results[0].get("c")
    return float(close) if close is not None else None


def fetch_option_chain(ticker: str, expiry: Optional[str] = None, max_expiries: int = 4) -> list[ChainSnapshot]:
    raw_results = _fetch_full_options_chain(ticker)

    spot = None
    by_expiry: dict[str, dict[float, dict]] = {}

    for item in raw_results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
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
        time.sleep(1)  # rate limit(429) 회피: 직전 옵션체인 호출과 시간차를 둔다
        spot = _fetch_prev_close(ticker)

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

    if not gex_by_strike:
        return {"call_wall": None, "put_wall": None, "gamma_flip": None, "net_gex_total": 0, "by_strike": []}

    strikes_at_or_above = {k: v for k, v in gex_by_strike.items() if k >= spot}
    strikes_at_or_below = {k: v for k, v in gex_by_strike.items() if k <= spot}

    call_candidates = strikes_at_or_above if strikes_at_or_above else gex_by_strike
    put_candidates = strikes_at_or_below if strikes_at_or_below else gex_by_strike

    call_wall = max(call_candidates, key=lambda k: call_candidates[k]["call_gex"])
    put_wall = min(put_candidates, key=lambda k: put_candidates[k]["put_gex"])

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

    if pos_pct > POS_THRESH and slope_pct > SLOPE_THRESH:
        stage, label = 2, "상승국면"
    elif pos_pct < -POS_THRESH and slope_pct < -SLOPE_THRESH:
        stage, label = 4, "하락국면"
    elif prior_slope_pct < -SLOPE_THRESH:
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
    })
    return result


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
) -> list[str]:
    lines: list[str] = []

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


def analyze_ticker(ticker: str, expiry: Optional[str] = None) -> dict:
    ticker = ticker.upper().strip()
    snapshots = fetch_option_chain(ticker, expiry=expiry)

    primary = snapshots[0]
    max_pain = compute_max_pain(primary.rows)

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

    narrative = build_narrative(
        spot=primary.spot,
        max_pain=max_pain["max_pain_strike"],
        call_wall=gex["call_wall"],
        put_wall=gex["put_wall"],
        gamma_flip=gex["gamma_flip"],
        regime=gex["regime"],
        expiry_used=primary.expiry,
    )

    stage_debug = None
    try:
        closes, stage_debug = fetch_daily_closes(ticker)
        stage_info = compute_stage(closes)
    except Exception as e:
        stage_info = {"stage": None, "label": None, "sma150": None, "slope_pct": None, "n_closes": 0}
        stage_debug = f"예외 발생: {e}"

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
        "narrative": narrative,
        "stage": stage_info["stage"],
        "stage_label": stage_info["label"],
        "stage_sma150": stage_info["sma150"],
        "stage_slope_pct": stage_info["slope_pct"],
        "stage_debug": stage_debug,
        "stage_n_closes": stage_info.get("n_closes"),
    }


def quick_gamma_flip(ticker: str) -> dict:
    """Top10 스캐너 전용 초경량 분석.

    API 요청 자체에 expiration_date 필터를 걸어서 근월물 계약만 딱 1페이지
    (최대 250개) 받아온다. 대부분의 경우 옵션체인 응답 자체에 현재가가
    포함되어 있어 추가 호출이 필요 없지만, 간혹 없는 경우에만 전일 종가로
    폴백한다. 이때 직전 호출과 시간차 없이 바로 이어서 요청하면 초당 요청
    제한(429 Too Many Requests)에 걸리기 쉬우므로, 폴백 호출 전 짧게 대기한다.
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

    spot = None
    strikes_dict: dict[float, dict] = {}
    nearest_expiry = None

    for item in results:
        details = item.get("details", {})
        exp = details.get("expiration_date")
        strike = details.get("strike_price")
        contract_type = details.get("contract_type")
        if not exp or strike is None or contract_type not in ("call", "put"):
            continue

        if nearest_expiry is None:
            nearest_expiry = exp
        if exp != nearest_expiry:
            continue

        if spot is None:
            underlying = item.get("underlying_asset", {})
            spot = underlying.get("price")

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

    if spot is None:
        time.sleep(1)
        spot = _fetch_prev_close(ticker)

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
        "expiry_used": nearest_expiry,
        "gamma_flip": gex["gamma_flip"],
        "regime": gex["regime"],
        "net_gex_total": gex["net_gex_total"],
    }


if __name__ == "__main__":
    import json
    import sys

    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    result = analyze_ticker(t)
    print(json.dumps({k: v for k, v in result.items() if k not in ("pain_curve", "gex_by_strike")}, indent=2))
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