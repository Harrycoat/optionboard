"""
scripts/dev_reentry_scanner.py

TOS ThinkScript Hull_Deviation_Reentry_v3와 동일한 로직을 파이썬으로 옮겨서,
active_universe.txt(유동성 상위 100종목)를 매일 스캔하고
"오늘의 매수 신호"(롱 재진입) / "오늘의 익절·방어 신호"(숏·청산) 후보를 뽑는다.

로직 요약 (TOS 지표와 동일):
  1) Hull21 이동평균선을 일봉 종가로 계산
  2) Dev% = (종가 - Hull21) / Hull21 * 100
  3) 최근 50일 Dev%의 평균/표준편차로 적응형 밴드(Bollinger식, ±2σ) 계산
  4) Dev%가 밴드를 벗어난(스트레치) 상태가 최소 2봉 이상 유지되다가,
     밴드 안쪽으로 히스테리시스 버퍼(35%)만큼 확실히 되돌아오는 첫 봉을 신호로 판단
  5) 롱 재진입은 "현재가 > Hull21" 추세 게이트를 반드시 통과해야 함
     (하락 추세에서 나오는 반등 잡음 신호를 원천 차단)
  6) 숏/청산(익절·CC 타이밍) 신호는 추세 게이트 없이, Call Wall 근접 여부와
     함께 상단 스트레치 되돌림만 확인 (기존 롱 포지션 관리용)
  7) Hull21 기울기 가속 여부로 CONTINUATION(추세 지속) vs REVERSION(단순 되돌림) 구분

active_universe.txt를 그대로 재사용하므로 별도 종목 리스트 관리가 필요 없고,
generate_leaders_report.py의 build_report() 안에서 이어서 호출된다.
"""

import statistics
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import fetch_daily_ohlc  # noqa: E402

BAND_LOOKBACK = 50
BAND_MULTIPLIER = 2.0
MIN_STRETCH_BARS = 2
HYSTERESIS_PCT = 0.35
SLOPE_LOOKBACK = 3
LOOKBACK_DAYS_REQUEST = 100  # Hull21 계산 + 50일 밴드 확보용 여유값

PER_TICKER_DELAY_SECONDS = 0.4
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = [5]


# ---------------------------------------------------------------------------
# Hull MA 계산 (가중이동평균 기반)
# ---------------------------------------------------------------------------
def _wma_series(values, period):
    """values 리스트에 대한 가중이동평균 시계열. 앞쪽 (period-1)개는 None."""
    n = len(values)
    series = [None] * n
    if n < period:
        return series
    weights = list(range(1, period + 1))
    wsum = sum(weights)
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        series[i] = sum(w * v for w, v in zip(weights, window)) / wsum
    return series


def hull_ma_series(closes, period=21):
    """Hull Moving Average 시계열 계산: WMA(2*WMA(n/2) - WMA(n), sqrt(n))"""
    half = max(1, round(period / 2))
    sqrt_p = max(1, round(period ** 0.5))

    wma_half = _wma_series(closes, half)
    wma_full = _wma_series(closes, period)

    diff = []
    for a, b in zip(wma_half, wma_full):
        diff.append(None if (a is None or b is None) else (2 * a - b))

    valid_start = next((i for i, v in enumerate(diff) if v is not None), None)
    hull = [None] * len(closes)
    if valid_start is None:
        return hull

    sub = diff[valid_start:]
    sub_wma = _wma_series(sub, sqrt_p)
    for i, v in enumerate(sub_wma):
        hull[valid_start + i] = v
    return hull


# ---------------------------------------------------------------------------
# 단일 종목 신호 계산
# ---------------------------------------------------------------------------
def _fetch_bars_with_retry(ticker):
    for attempt in range(MAX_RETRIES + 1):
        try:
            bars, _debug = fetch_daily_ohlc(ticker, lookback_days=LOOKBACK_DAYS_REQUEST)
            return bars
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[attempt])
            else:
                print(f"    [dev_reentry] 일봉 조회 실패 ({ticker}): {e}")
                return None


def compute_dev_reentry_signal(ticker, bars):
    """bars(오래된 순 → 최신 순 일봉 리스트)로 Dev% 재진입 신호를 계산한다.
    신호가 없으면 None, 있으면 dict를 반환한다.
    """
    if not bars or len(bars) < 21 + BAND_LOOKBACK + SLOPE_LOOKBACK * 2:
        return None

    closes = [b["close"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]

    hull = hull_ma_series(closes, period=21)
    if hull[-1] is None or hull[-2] is None:
        return None

    # Dev% 시계열 (Hull 계산 안 된 구간은 제외)
    dev_pct = []
    for c, h in zip(closes, hull):
        dev_pct.append(None if h is None else (c - h) / h * 100)

    valid_dev = [d for d in dev_pct if d is not None]
    if len(valid_dev) < BAND_LOOKBACK + MIN_STRETCH_BARS + 2:
        return None

    recent_band_window = valid_dev[-BAND_LOOKBACK:]
    dev_avg = statistics.fmean(recent_band_window)
    dev_std = statistics.pstdev(recent_band_window)
    upper_band = dev_avg + dev_std * BAND_MULTIPLIER
    lower_band = dev_avg - dev_std * BAND_MULTIPLIER
    band_width = upper_band - lower_band
    if band_width <= 0:
        return None

    # 최근 dev_pct만 뒤에서부터 다시 정렬 (None 제거된 상태로, 시간순 유지)
    dev_tail = valid_dev[-(MIN_STRETCH_BARS + 6):]  # 여유있게 최근 구간만
    if len(dev_tail) < MIN_STRETCH_BARS + 2:
        return None

    today_dev = dev_tail[-1]
    prev_dev = dev_tail[-2]

    # 스트레치 연속 카운트 (오늘 이전 봉들 기준)
    stretch_up_count = 0
    stretch_down_count = 0
    for d in reversed(dev_tail[:-1]):
        if d >= upper_band:
            stretch_up_count += 1
        else:
            break
    for d in reversed(dev_tail[:-1]):
        if d <= lower_band:
            stretch_down_count += 1
        else:
            break

    was_stretched_up = stretch_up_count >= MIN_STRETCH_BARS
    was_stretched_down = stretch_down_count >= MIN_STRETCH_BARS

    return_level_from_up = upper_band - band_width * HYSTERESIS_PCT
    return_level_from_down = lower_band + band_width * HYSTERESIS_PCT

    contracted_from_up = today_dev <= return_level_from_up and today_dev < prev_dev
    contracted_from_down = today_dev >= return_level_from_down and today_dev > prev_dev

    long_reentry_raw = was_stretched_down and contracted_from_down
    short_exit_raw = was_stretched_up and contracted_from_up

    if not long_reentry_raw and not short_exit_raw:
        return None

    # ---- 추세 게이트: 롱 재진입은 현재가 > Hull21 이어야만 유효 ----
    spot = closes[-1]
    hull_now = hull[-1]
    above_hull21 = spot > hull_now

    if long_reentry_raw and not above_hull21:
        long_reentry_raw = False

    if not long_reentry_raw and not short_exit_raw:
        return None

    # ---- Hull 기울기 가속 여부 (CONTINUATION vs REVERSION) ----
    hull_valid = [h for h in hull if h is not None]
    slope_accelerating = None
    if len(hull_valid) >= SLOPE_LOOKBACK * 2 + 1:
        now_slope = (hull_valid[-1] - hull_valid[-1 - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK
        prev_slope = (
            hull_valid[-1 - SLOPE_LOOKBACK] - hull_valid[-1 - SLOPE_LOOKBACK * 2]
        ) / SLOPE_LOOKBACK
        slope_accelerating = abs(now_slope) > abs(prev_slope)

    # ---- 거래량 확인 (스트레치 구간 평균 거래량 vs 20일 평균) ----
    vol_avg_20 = statistics.fmean(volumes[-20:]) if len(volumes) >= 20 else None
    stretch_len = stretch_down_count if long_reentry_raw else stretch_up_count
    stretch_len = max(stretch_len, 1)
    vol_during_stretch = statistics.fmean(volumes[-(stretch_len + 1) : -1]) if stretch_len < len(volumes) else None
    volume_confirmed = (
        vol_avg_20 is not None
        and vol_during_stretch is not None
        and vol_during_stretch >= vol_avg_20
    )

    # ---- CONTINUATION 스코어 (0~2) ----
    score = 0
    if slope_accelerating:
        score += 1
    if volume_confirmed:
        score += 1
    is_continuation = score >= 1  # 기울기 가속 또는 거래량 둘 중 하나만 맞아도 지속형으로 취급

    result = {
        "ticker": ticker,
        "spot": round(spot, 2),
        "hull21": round(hull_now, 2),
        "dev_pct": round(today_dev, 2),
        "band_upper": round(upper_band, 2),
        "band_lower": round(lower_band, 2),
        "slope_accelerating": slope_accelerating,
        "volume_confirmed": volume_confirmed,
        "continuation_score": score,
        "is_continuation": is_continuation,
        "signal_type": "long_reentry" if long_reentry_raw else "short_exit",
    }
    return result


# ---------------------------------------------------------------------------
# 전체 유니버스 스캔
# ---------------------------------------------------------------------------
def build_dev_reentry_signals(tickers):
    """active_universe(100종목)를 스캔해서 롱 재진입 / 숏·청산 신호를 나눠 반환한다."""
    print(f"\nDev% 재진입 스캐너: {len(tickers)}개 종목 스캔 시작")

    long_candidates = []
    short_candidates = []
    skipped = 0

    for i, ticker in enumerate(tickers, 1):
        if i % 20 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")

        bars = _fetch_bars_with_retry(ticker)
        if not bars:
            skipped += 1
            time.sleep(PER_TICKER_DELAY_SECONDS)
            continue

        try:
            signal = compute_dev_reentry_signal(ticker, bars)
        except Exception as e:
            print(f"    [dev_reentry] 계산 실패 ({ticker}): {e}")
            signal = None

        if signal:
            if signal["signal_type"] == "long_reentry":
                long_candidates.append(signal)
            else:
                short_candidates.append(signal)
        else:
            skipped += 1

        time.sleep(PER_TICKER_DELAY_SECONDS)

    # CONTINUATION 신호 우선, 그다음 |Dev%| 큰 순
    long_candidates.sort(key=lambda x: (not x["is_continuation"], -abs(x["dev_pct"])))
    short_candidates.sort(key=lambda x: (not x["is_continuation"], -abs(x["dev_pct"])))

    print(
        f"Dev% 재진입 스캐너 완료: 롱 재진입 {len(long_candidates)}개 / "
        f"숏·청산 {len(short_candidates)}개 / 스킵 {skipped}개"
    )

    return {
        "long_reentry": long_candidates[:15],
        "short_exit": short_candidates[:15],
    }
