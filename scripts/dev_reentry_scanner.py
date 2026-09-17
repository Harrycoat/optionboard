"""오늘의 기술적 진입 후보 스캐너.

유동성 상위 종목에서 오늘 새로 발생한 두 이벤트를 찾는다.
1) MA50이 MA100을 상향 돌파한 신규 추세
2) 상승 추세 안에서 Hull21 Dev%가 하단 밴드로부터 재진입한 눌림목

평균 거래대금과 거래량을 확인하고, 진입 후보에는 GEX 위치를 추가해 순위를 정한다.
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import analyze_ticker, fetch_daily_ohlc  # noqa: E402

BAND_LOOKBACK = 50
BAND_MULTIPLIER = 2.0
HULL_PERIOD = 21
SLOPE_LOOKBACK = 3
LOOKBACK_DAYS_REQUEST = 200

MIN_PRICE = 5.0
MIN_AVG_DOLLAR_VOLUME = 20_000_000
MIN_VOLUME_RATIO = 1.2
MAX_ENTRY_RESULTS = 10
MAX_WATCH_RESULTS = 10
MAX_GEX_LOOKUPS = 10

PER_TICKER_DELAY_SECONDS = 0.4
GEX_TICKER_DELAY_SECONDS = 0.5
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = [5]

SIGNAL_LABELS = {
    "combined": "최우선: 신규추세 + Hull21",
    "trend_cross": "MA50/100 신규추세",
    "hull_reentry": "Hull21 눌림 재진입",
    "watch": "오늘의 관찰 후보",
}


def _wma_series(values, period):
    series = [None] * len(values)
    if len(values) < period:
        return series
    weights = list(range(1, period + 1))
    weight_sum = sum(weights)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        series[i] = sum(w * value for w, value in zip(weights, window)) / weight_sum
    return series


def _sma_series(values, period):
    series = [None] * len(values)
    if len(values) < period:
        return series
    running_sum = sum(values[:period])
    series[period - 1] = running_sum / period
    for i in range(period, len(values)):
        running_sum += values[i] - values[i - period]
        series[i] = running_sum / period
    return series


def hull_ma_series(closes, period=HULL_PERIOD):
    half = max(1, round(period / 2))
    sqrt_period = max(1, round(period**0.5))
    wma_half = _wma_series(closes, half)
    wma_full = _wma_series(closes, period)
    difference = [
        None if a is None or b is None else 2 * a - b
        for a, b in zip(wma_half, wma_full)
    ]
    valid_start = next((i for i, value in enumerate(difference) if value is not None), None)
    hull = [None] * len(closes)
    if valid_start is None:
        return hull
    sub_hull = _wma_series(difference[valid_start:], sqrt_period)
    for i, value in enumerate(sub_hull):
        hull[valid_start + i] = value
    return hull


def _rolling_dev_bands(dev_pct):
    """각 날짜 당시의 최근 50개 Dev%로 밴드를 계산한다."""
    lower = [None] * len(dev_pct)
    upper = [None] * len(dev_pct)
    for i in range(len(dev_pct)):
        if dev_pct[i] is None:
            continue
        start = i - BAND_LOOKBACK + 1
        if start < 0:
            continue
        window = dev_pct[start : i + 1]
        if any(value is None for value in window):
            continue
        average = statistics.fmean(window)
        stddev = statistics.pstdev(window)
        if stddev <= 0:
            continue
        lower[i] = average - stddev * BAND_MULTIPLIER
        upper[i] = average + stddev * BAND_MULTIPLIER
    return lower, upper


def _fetch_bars_with_retry(ticker):
    for attempt in range(MAX_RETRIES + 1):
        try:
            bars, _debug = fetch_daily_ohlc(ticker, lookback_days=LOOKBACK_DAYS_REQUEST)
            return bars
        except Exception as exc:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
            else:
                print(f"    [technical_signal] 일봉 조회 실패 ({ticker}): {exc}")
                return None


def _round_or_none(value, digits=2):
    return None if value is None else round(float(value), digits)


def compute_technical_signal(ticker, bars):
    """오늘 발생한 신규추세 또는 Hull21 재진입 이벤트를 계산한다."""
    if not bars or len(bars) < 105:
        return None

    closes = [float(bar["close"]) for bar in bars]
    volumes = [float(bar.get("volume") or 0) for bar in bars]
    spot = closes[-1]

    hull = hull_ma_series(closes)
    sma50 = _sma_series(closes, 50)
    sma100 = _sma_series(closes, 100)
    required = (hull[-1], hull[-2], sma50[-1], sma50[-2], sma100[-1], sma100[-2])
    if any(value is None for value in required):
        return None

    dev_pct = [
        None if hull_value is None else (close - hull_value) / hull_value * 100
        for close, hull_value in zip(closes, hull)
    ]
    lower_band, upper_band = _rolling_dev_bands(dev_pct)
    band_values = (dev_pct[-1], dev_pct[-2], lower_band[-1], lower_band[-2])
    if any(value is None for value in band_values):
        return None

    previous_volumes = volumes[-21:-1]
    avg_volume_20 = statistics.fmean(previous_volumes) if previous_volumes else 0
    volume_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 > 0 else 0
    avg_dollar_volume = avg_volume_20 * spot
    liquid = spot >= MIN_PRICE and avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME
    volume_confirmed = volume_ratio >= MIN_VOLUME_RATIO

    trend_cross = sma50[-2] <= sma100[-2] and sma50[-1] > sma100[-1]
    trend_active = (
        sma50[-1] > sma100[-1]
        and sma50[-6] is not None
        and sma50[-1] > sma50[-6]
    )

    today_dev = dev_pct[-1]
    previous_dev = dev_pct[-2]
    hull_reentry = (
        previous_dev <= lower_band[-2]
        and today_dev > lower_band[-1]
        and today_dev > previous_dev
    )

    now_hull_slope = (hull[-1] - hull[-1 - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK
    previous_hull_slope = (
        hull[-1 - SLOPE_LOOKBACK] - hull[-1 - SLOPE_LOOKBACK * 2]
    ) / SLOPE_LOOKBACK
    hull_rising = now_hull_slope > 0 and now_hull_slope > previous_hull_slope
    above_hull21 = spot > hull[-1]

    trend_entry = trend_cross and spot > sma50[-1] and volume_confirmed and liquid
    hull_entry = (
        hull_reentry
        and trend_active
        and above_hull21
        and hull_rising
        and volume_confirmed
        and liquid
    )

    # 과거 15일 신호를 반복하지 않고 오늘 발생한 이벤트만 남긴다.
    if not trend_cross and not hull_reentry:
        return None

    if trend_entry and hull_entry:
        signal_type = "combined"
    elif trend_entry:
        signal_type = "trend_cross"
    elif hull_entry:
        signal_type = "hull_reentry"
    else:
        signal_type = "watch"

    stage = 2 if signal_type != "watch" else 1
    score = 0.0
    if trend_entry:
        score += 45
    if hull_entry:
        score += 35
    if trend_active:
        score += 10
    score += min(volume_ratio, 3.0) / 3.0 * 10
    if avg_dollar_volume >= 100_000_000:
        score += 5

    return {
        "ticker": ticker,
        "spot": round(spot, 2),
        "signal_type": signal_type,
        "signal_label": SIGNAL_LABELS[signal_type],
        "stage": stage,
        "stage_label": SIGNAL_LABELS[signal_type],
        "score": round(score, 1),
        "hull21": round(hull[-1], 2),
        "dev_pct": round(today_dev, 2),
        "band_upper": _round_or_none(upper_band[-1]),
        "band_lower": _round_or_none(lower_band[-1]),
        "ma50": round(sma50[-1], 2),
        "ma100": round(sma100[-1], 2),
        "trend_cross": trend_cross,
        "trend_active": trend_active,
        "hull_reentry": hull_reentry,
        "above_hull21": above_hull21,
        "hull_rising": hull_rising,
        "slope_accelerating": hull_rising,
        "volume_confirmed": volume_confirmed,
        "volume_ratio": round(volume_ratio, 2),
        "avg_dollar_volume": round(avg_dollar_volume),
        "liquidity_confirmed": liquid,
    }


def _add_gex_context(candidate):
    """진입 후보에 GEX 위치를 추가한다. 실패해도 기술 신호는 유지한다."""
    enriched = dict(candidate)
    try:
        data = analyze_ticker(candidate["ticker"], skip_stage=True)
        spot = float(data.get("spot") or candidate["spot"])
        put_wall = data.get("put_wall")
        call_wall = data.get("call_wall")
        gamma_flip = data.get("gamma_flip")
        above_put_wall = put_wall is not None and spot >= float(put_wall)
        above_gamma_flip = gamma_flip is not None and spot >= float(gamma_flip)
        call_wall_upside_pct = (
            (float(call_wall) - spot) / spot * 100 if call_wall is not None and spot else None
        )

        gex_score = 0
        if above_put_wall:
            gex_score += 6
        if above_gamma_flip:
            gex_score += 6
        if call_wall_upside_pct is not None and call_wall_upside_pct >= 2:
            gex_score += 3

        enriched.update({
            "put_wall": _round_or_none(put_wall),
            "call_wall": _round_or_none(call_wall),
            "gamma_flip": _round_or_none(gamma_flip),
            "above_put_wall": above_put_wall,
            "above_gamma_flip": above_gamma_flip,
            "call_wall_upside_pct": _round_or_none(call_wall_upside_pct),
            "gex_confirmed": above_put_wall and above_gamma_flip,
            "gex_available": True,
            "score": round(candidate["score"] + gex_score, 1),
        })
    except Exception as exc:
        print(f"    [technical_signal] GEX 조회 실패 ({candidate['ticker']}): {exc}")
        enriched.update({"gex_available": False, "gex_confirmed": False})
    return enriched


def build_dev_reentry_signals(tickers):
    """기존 키를 유지하면서 신규추세·눌림목·관찰 후보를 반환한다."""
    print(f"\n오늘의 기술적 진입 후보: {len(tickers)}개 종목 스캔 시작")
    candidates = []

    for index, ticker in enumerate(tickers, 1):
        if index % 20 == 0 or index == 1:
            print(f"  진행: {index}/{len(tickers)} ({ticker})")
        bars = _fetch_bars_with_retry(ticker)
        if bars:
            try:
                signal = compute_technical_signal(ticker, bars)
                if signal:
                    candidates.append(signal)
            except Exception as exc:
                print(f"    [technical_signal] 계산 실패 ({ticker}): {exc}")
        time.sleep(PER_TICKER_DELAY_SECONDS)

    entries = sorted(
        (candidate for candidate in candidates if candidate["stage"] == 2),
        key=lambda candidate: -candidate["score"],
    )
    watches = sorted(
        (candidate for candidate in candidates if candidate["stage"] == 1),
        key=lambda candidate: -candidate["score"],
    )

    enriched_entries = []
    for candidate in entries[:MAX_GEX_LOOKUPS]:
        enriched_entries.append(_add_gex_context(candidate))
        time.sleep(GEX_TICKER_DELAY_SECONDS)
    enriched_entries.sort(key=lambda candidate: -candidate["score"])

    top_pick = enriched_entries[0] if enriched_entries else None
    trend_candidates = [
        candidate for candidate in enriched_entries
        if candidate["signal_type"] in ("trend_cross", "combined")
    ]
    hull_entries = [
        candidate for candidate in enriched_entries
        if candidate["signal_type"] in ("hull_reentry", "combined")
    ]
    watch_candidates = watches[:MAX_WATCH_RESULTS]
    legacy_list = enriched_entries[:MAX_ENTRY_RESULTS] + watch_candidates

    print(
        "오늘의 기술적 진입 후보 완료: "
        f"진입 {len(enriched_entries)}개 / 관찰 {len(watch_candidates)}개 / "
        f"최우선 {top_pick['ticker'] if top_pick else '없음'}"
    )
    return {
        "top_pick": top_pick,
        "trend_candidates": trend_candidates[:MAX_ENTRY_RESULTS],
        "hull_entries": hull_entries[:MAX_ENTRY_RESULTS],
        "watch_candidates": watch_candidates,
        "long_reentry": legacy_list,
        "short_exit": [],
    }
