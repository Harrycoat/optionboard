"""
scripts/dev_reentry_scanner.py

TOS ThinkScript Hull_Deviation_Reentry_v3와 동일한 로직을 파이썬으로 옮겨서,
active_universe.txt(유동성 상위 100종목)를 매일 스캔하고 "오늘의 매수 신호"를
HULL 스테이지 1~2로 뽑는다. (매도 사이드는 사용하지 않음 — 매수 관찰/진입만 표시)

로직 요약:
  1) Hull21 이동평균선을 일봉 종가로 계산
  2) Dev% = (종가 - Hull21) / Hull21 * 100
  3) 최근 50일 Dev%의 평균/표준편차로 적응형 밴드(Bollinger식, ±2σ) 계산
  4) 매수 사이드만 2단계로 표시한다:
       - HULL 스테이지1(관찰타이밍): 최근에 밴드 하단을 이탈(하락 스트레치)했다가
         오늘 밴드 안으로 복귀한 상태 — 가장 이른 관찰 단계
       - HULL 스테이지2(진입타이밍): 스테이지1 + 오늘 상승 전환 + 현재가>Hull21
         (추세 게이트) + 기울기 가속 또는 거래량 확인 중 하나 이상(모멘텀 확인)
  5) 스테이지2로 올라가려면 반드시 모멘텀 확인(기울기 가속 또는 거래량 확인)이
     있어야 한다 — 패턴 모양만 맞고 실제 힘이 없는 종목이 최상위 단계로
     잘못 분류되는 걸 막기 위한 조건.

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
SLOPE_LOOKBACK = 3
STAGE_LOOKBACK_DAYS = 15  # 최근 스트레치~복귀 패턴을 찾기 위해 살펴보는 기간
LOOKBACK_DAYS_REQUEST = 130  # Hull21 계산 + 50일 밴드 + 단계 판별 여유값

PER_TICKER_DELAY_SECONDS = 0.4
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = [5]

STAGE_LABELS = {
    1: "HULL 스테이지1(관찰타이밍)",
    2: "HULL 스테이지2(진입타이밍)",
}


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
# 데이터 조회
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


# ---------------------------------------------------------------------------
# 단일 종목 매수 사이드 2단계 판별 (스테이지1 관찰 / 스테이지2 진입)
# ---------------------------------------------------------------------------
def compute_dev_stage_signal(ticker, bars):
    """bars(오래된 순 → 최신 순 일봉 리스트)로 HULL 스테이지 1~2(매수 사이드)를
    계산한다. 신호가 없으면 None.
    """
    min_bars_needed = 21 + BAND_LOOKBACK + STAGE_LOOKBACK_DAYS + 2
    if not bars or len(bars) < min_bars_needed:
        return None

    closes = [b["close"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]

    hull = hull_ma_series(closes, period=21)
    if hull[-1] is None or hull[-2] is None:
        return None

    dev_pct = []
    for c, h in zip(closes, hull):
        dev_pct.append(None if h is None else (c - h) / h * 100)

    valid_dev = [d for d in dev_pct if d is not None]
    if len(valid_dev) < BAND_LOOKBACK + STAGE_LOOKBACK_DAYS + 2:
        return None

    recent_band_window = valid_dev[-BAND_LOOKBACK:]
    dev_avg = statistics.fmean(recent_band_window)
    dev_std = statistics.pstdev(recent_band_window)
    lower_band = dev_avg - dev_std * BAND_MULTIPLIER
    upper_band = dev_avg + dev_std * BAND_MULTIPLIER
    band_width = upper_band - lower_band
    if band_width <= 0:
        return None

    dev_tail = valid_dev[-(STAGE_LOOKBACK_DAYS + 1):]
    today_dev = dev_tail[-1]
    prev_dev = dev_tail[-2]
    history = dev_tail[:-1]  # 오늘 이전 구간 (스트레치~복귀 패턴 탐색 범위)

    was_stretched_down = any(d <= lower_band for d in history)

    spot = closes[-1]
    hull_now = hull[-1]
    above_hull21 = spot > hull_now
    rising_today = today_dev > prev_dev

    # ---- Hull 기울기 가속 여부 + 거래량 확인 (모멘텀 확인용) ----
    hull_valid = [h for h in hull if h is not None]
    slope_accelerating = None
    if len(hull_valid) >= SLOPE_LOOKBACK * 2 + 1:
        now_slope = (hull_valid[-1] - hull_valid[-1 - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK
        prev_slope = (
            hull_valid[-1 - SLOPE_LOOKBACK] - hull_valid[-1 - SLOPE_LOOKBACK * 2]
        ) / SLOPE_LOOKBACK
        slope_accelerating = abs(now_slope) > abs(prev_slope)

    vol_avg_20 = statistics.fmean(volumes[-20:]) if len(volumes) >= 20 else None
    vol_today = volumes[-1] if volumes else None
    volume_confirmed = (
        vol_avg_20 is not None and vol_today is not None and vol_today >= vol_avg_20
    )

    momentum_confirmed = bool(slope_accelerating) or bool(volume_confirmed)

    stage = None

    # ---- 매수 사이드: 스테이지1(관찰) → 스테이지2(진입) ----
    if was_stretched_down and today_dev > lower_band:
        stage = 1
        if rising_today and above_hull21 and momentum_confirmed:
            stage = 2

    if stage is None:
        return None

    return {
        "ticker": ticker,
        "spot": round(spot, 2),
        "hull21": round(hull_now, 2),
        "dev_pct": round(today_dev, 2),
        "band_upper": round(upper_band, 2),
        "band_lower": round(lower_band, 2),
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "above_hull21": above_hull21,
        "slope_accelerating": slope_accelerating,
        "volume_confirmed": volume_confirmed,
    }


# ---------------------------------------------------------------------------
# 전체 유니버스 스캔
# ---------------------------------------------------------------------------
def build_dev_reentry_signals(tickers):
    """active_universe(100종목)를 스캔해서 매수 사이드(스테이지1~2) 신호만
    반환한다. (매도 사이드는 더 이상 사용하지 않지만, generate_leaders_report.py와
    index.html이 참조하는 JSON 키 구조는 그대로 유지하기 위해 short_exit는
    항상 빈 리스트로 반환한다.)"""
    print(f"\nDev% 재진입 스캐너: {len(tickers)}개 종목 스캔 시작")

    long_candidates = []
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
            signal = compute_dev_stage_signal(ticker, bars)
        except Exception as e:
            print(f"    [dev_reentry] 계산 실패 ({ticker}): {e}")
            signal = None

        if signal:
            long_candidates.append(signal)
        else:
            skipped += 1

        time.sleep(PER_TICKER_DELAY_SECONDS)

    # 스테이지 높은 순 → |Dev%| 큰 순으로 정렬
    long_candidates.sort(key=lambda x: (-x["stage"], -abs(x["dev_pct"])))

    stage2_n = sum(1 for c in long_candidates if c["stage"] == 2)
    stage1_n = sum(1 for c in long_candidates if c["stage"] == 1)

    print(
        f"Dev% 재진입 스캐너 완료: 매수 {len(long_candidates)}개 "
        f"(스테이지2 {stage2_n} / 스테이지1 {stage1_n}) / 스킵 {skipped}개"
    )

    return {
        "long_reentry": long_candidates[:15],
        "short_exit": [],
    }
