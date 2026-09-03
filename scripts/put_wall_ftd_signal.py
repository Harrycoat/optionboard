"""
scripts/put_wall_ftd_signal.py

해리님이 정리하신 3단계 스윙 프레임워크를 "일봉" 데이터에 그대로 적용해서
계산해주는 도구.

  1) 진입(Entry):
     최근 눌림목 저점이 '위클리 Put Wall' 근처(그 위/아래 일정 범위 안)에서
     형성된 뒤, FTD(Follow-Through Day)식 확인 신호 — 저점을 찍은 지 최소
     며칠(FTD_MIN_DAY_INDEX)이 지난 시점에, 거래량이 전일 대비 늘면서 주가가
     기준치(FTD_MIN_GAIN_PCT) 이상 강하게 오른 날 — 이 나왔는지 확인한다.
     (IBD의 지수 레벨 Follow-Through Day 개념을 개별 종목 일봉에 맞게
     단순화해서 옮긴 것이며, 지수 자체의 FTD와 동일한 신뢰도를 가진다고
     보장하지는 않는다 — 참고 신호로만 사용할 것.)

  2) 보유 중 관리(Weekly Checkpoint):
     Put Wall/Call Wall은 옵션 만기 구조상 매주 달라지므로, 이 스크립트를
     주 1회(예: 매주 월요일 장 시작 전) 다시 실행해서 최신 레벨을 불러오고
     "여전히 상승 구조 안에 있는지"(현재가가 Put Wall 위에 있는지, Call Wall
     까지 여유가 있는지, 감마 체제가 어떤지, Hull21이 Hull50 위인지, 150일선
     위인지)를 한 번에 점검한다.

  3) 청산(Exit):
     캘린더(예: "한 달 지났으니 판다") 기준이 아니라, 아래 두 가지
     "구조적 붕괴" 신호로만 판단한다.
       - Hull21이 Hull50을 아래로 교차하는 데드크로스
       - 종가가 150일 이동평균선을 이탈

이 스크립트는 옵션 체인 기반 값(Put Wall/Call Wall/감마 체제)은
api/options_engine.py의 analyze_ticker()를, 일봉 OHLCV는 같은 파일의
fetch_daily_ohlc()를 그대로 재사용한다 — 사이트의 계산 방식과 100% 동일하게
맞추기 위함. Hull 이동평균 계산은 scripts/dev_reentry_scanner.py의
hull_ma_series()를 그대로 재사용한다 (Hull21은 물론 Hull50에도 그대로 쓸 수
있게 period 인자로 되어 있음).

사용법:
  python scripts/put_wall_ftd_signal.py SPCX PLTR TSLA
  (인자 없이 실행하면 scripts/watchlist.txt에 등록된 종목들을 분석한다)

⚠️ 참고용 분석 도구이며, 매수/매도를 지시하는 투자자문이 아니다. 실전 매매
판단과 책임은 본인에게 있다.
"""

import os
import statistics
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.dirname(__file__))

from options_engine import analyze_ticker, fetch_daily_ohlc  # noqa: E402
from dev_reentry_scanner import hull_ma_series  # noqa: E402  (Hull21에도 쓰던 걸 그대로 재사용)

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.txt")

# ---------------------------------------------------------------------------
# 파라미터
# ---------------------------------------------------------------------------
LOOKBACK_DAYS_REQUEST = 400  # 150일선 계산 여유까지 포함한 일봉 조회 기간(달력일)
SMA_PERIOD = 150
HULL_FAST_PERIOD = 21
HULL_SLOW_PERIOD = 50

PULLBACK_LOOKBACK_DAYS = 20   # 최근 눌림목 저점을 찾는 구간(거래일)
FTD_LOOKAHEAD_DAYS = 10       # 저점 이후 FTD를 찾는 최대 구간(거래일)
FTD_MIN_DAY_INDEX = 4         # 저점 이후 최소 며칠째부터 FTD로 인정할지 (IBD 관례)
FTD_MIN_GAIN_PCT = 1.5        # FTD로 인정할 최소 상승률(%)
PUT_WALL_PROXIMITY_PCT = 4.0  # 저점이 Put Wall 대비 이 범위(%) 안이면 "Put Wall 근처"로 인정


# ---------------------------------------------------------------------------
# 순수 계산 함수 (일봉만 있으면 계산 가능 — 옵션 체인 불필요)
# ---------------------------------------------------------------------------
def sma_series(values, period):
    """values 리스트에 대한 단순이동평균 시계열. 앞쪽 (period-1)개는 None."""
    n = len(values)
    series = [None] * n
    if n < period:
        return series
    window_sum = sum(values[:period])
    series[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        series[i] = window_sum / period
    return series


def find_ftd_entry_signal(bars, put_wall):
    """최근 눌림목 저점 이후 FTD식 확인 신호가 있는지 찾는다.
    있으면 dict, 없으면 None.
    """
    closes = [b["close"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]
    dates = [b["time"] for b in bars]
    n = len(closes)

    total_window = PULLBACK_LOOKBACK_DAYS + FTD_LOOKAHEAD_DAYS
    if n < total_window + 1:
        return None

    recent_start = n - total_window
    # FTD가 저점 이후 최소 FTD_MIN_DAY_INDEX일 뒤에 나올 여유가 있어야 하므로,
    # 구간의 맨 끝 FTD_MIN_DAY_INDEX일은 저점 후보에서 제외한다.
    low_search_end = n - FTD_MIN_DAY_INDEX
    if low_search_end <= recent_start:
        return None

    low_slice = closes[recent_start:low_search_end]
    low_offset = low_slice.index(min(low_slice))
    low_idx = recent_start + low_offset
    low_close = closes[low_idx]
    low_date = dates[low_idx]

    near_put_wall = None
    put_wall_dist_pct = None
    if put_wall:
        put_wall_dist_pct = round((low_close - put_wall) / put_wall * 100, 2)
        near_put_wall = -PUT_WALL_PROXIMITY_PCT <= put_wall_dist_pct <= PUT_WALL_PROXIMITY_PCT

    # 저점 이후 구간에서, 가장 최근 FTD 후보부터 역방향으로 탐색
    ftd_day = None
    earliest_ftd_idx = low_idx + FTD_MIN_DAY_INDEX
    for i in range(n - 1, earliest_ftd_idx - 1, -1):
        if i <= 0:
            break
        prev_close = closes[i - 1]
        if not prev_close:
            continue
        day_gain_pct = (closes[i] - prev_close) / prev_close * 100
        vol_up_vs_prev_day = volumes[i] > volumes[i - 1]
        vol20 = statistics.fmean(volumes[max(0, i - 20):i]) if i >= 5 else None
        vol_above_20d_avg = vol20 is not None and volumes[i] >= vol20

        if day_gain_pct >= FTD_MIN_GAIN_PCT and vol_up_vs_prev_day:
            ftd_day = {
                "date": dates[i],
                "day_index_since_low": i - low_idx,
                "gain_pct": round(day_gain_pct, 2),
                "volume_above_20d_avg": vol_above_20d_avg,
                "is_most_recent_bar": i == n - 1,
            }
            break

    if ftd_day is None:
        return None

    return {
        "low_date": low_date,
        "low_close": round(low_close, 2),
        "put_wall": put_wall,
        "put_wall_dist_pct": put_wall_dist_pct,
        "near_put_wall": near_put_wall,
        "ftd": ftd_day,
        "entry_confirmed": bool(near_put_wall) and ftd_day is not None,
    }


def structural_exit_signals(closes, dates, hull_fast, hull_slow, sma150):
    """청산 근거(캘린더 아님)를 리스트로 반환한다. 비어있으면 아직 구조 붕괴 아님."""
    reasons = []

    if (
        hull_fast[-1] is not None
        and hull_slow[-1] is not None
        and hull_fast[-2] is not None
        and hull_slow[-2] is not None
    ):
        was_above_or_equal = hull_fast[-2] >= hull_slow[-2]
        now_below = hull_fast[-1] < hull_slow[-1]
        if was_above_or_equal and now_below:
            reasons.append(
                f"⛔ Hull{HULL_FAST_PERIOD}/{HULL_SLOW_PERIOD} 데드크로스 발생 ({dates[-1]}) — "
                f"Hull{HULL_FAST_PERIOD} {round(hull_fast[-1], 2)} < Hull{HULL_SLOW_PERIOD} {round(hull_slow[-1], 2)}"
            )
        elif now_below:
            reasons.append(
                f"⚠️ 이미 데드크로스 상태 — Hull{HULL_FAST_PERIOD} {round(hull_fast[-1], 2)} < "
                f"Hull{HULL_SLOW_PERIOD} {round(hull_slow[-1], 2)} (교차 시점은 더 이전)"
            )

    if sma150[-1] is not None and closes[-1] < sma150[-1]:
        reasons.append(
            f"⛔ 종가({closes[-1]})가 150일선({round(sma150[-1], 2)}) 아래로 이탈"
        )

    return reasons


# ---------------------------------------------------------------------------
# 종목 1개 분석 (일봉 + 옵션 체인 결합)
# ---------------------------------------------------------------------------
def analyze_one(ticker):
    ticker = ticker.upper().strip()
    bars, debug = fetch_daily_ohlc(ticker, lookback_days=LOOKBACK_DAYS_REQUEST)
    if not bars or len(bars) < SMA_PERIOD + 5:
        print(f"\n=== {ticker} ===")
        print(f"  일봉 데이터 부족으로 계산 불가 ({debug})")
        return

    closes = [b["close"] for b in bars]
    dates = [b["time"] for b in bars]

    hull_fast = hull_ma_series(closes, period=HULL_FAST_PERIOD)
    hull_slow = hull_ma_series(closes, period=HULL_SLOW_PERIOD)
    sma150 = sma_series(closes, period=SMA_PERIOD)

    # 옵션 체인 기반 값(Put Wall/Call Wall/감마)은 실시간 API 호출이 필요하므로
    # 실패해도(휴장/API 문제 등) 나머지 일봉 계산은 계속 보여준다.
    live = None
    live_error = None
    try:
        live = analyze_ticker(ticker, skip_stage=True)
    except Exception as e:
        live_error = str(e)

    spot = live["spot"] if live else closes[-1]
    put_wall = live["put_wall"] if live else None
    call_wall = live["call_wall"] if live else None
    regime = live["regime"] if live else None

    print(f"\n=== {ticker} ===")
    print(f"  현재가(참고): {spot}" + ("  (옵션 체인 조회 실패 — 최근 종가로 대체)" if live_error else ""))
    if live_error:
        print(f"  ⚠️ 옵션 체인 조회 실패: {live_error}")

    # ---------------- 1) 진입: Put Wall 바운스 + FTD ----------------
    entry = find_ftd_entry_signal(bars, put_wall)
    print("\n  [1] 진입 신호 (Put Wall 바운스 + FTD식 확인)")
    if entry is None:
        print(f"      최근 {PULLBACK_LOOKBACK_DAYS + FTD_LOOKAHEAD_DAYS}거래일 안에 조건에 맞는 FTD 캔들이 없음")
    else:
        near = entry["near_put_wall"]
        near_txt = "예" if near else ("아니오" if near is False else "Put Wall 값 없음")
        print(f"      눌림목 저점: {entry['low_date']} 종가 {entry['low_close']}"
              + (f" (Put Wall {put_wall} 대비 {entry['put_wall_dist_pct']:+.1f}%)" if put_wall else ""))
        print(f"      Put Wall 근처 여부: {near_txt}")
        f = entry["ftd"]
        recency = " ← 오늘(가장 최근 봉)" if f["is_most_recent_bar"] else ""
        print(f"      FTD 후보: {f['date']} (+{f['gain_pct']}%, 저점 후 {f['day_index_since_low']}일째, "
              f"거래량 20일평균 이상: {'예' if f['volume_above_20d_avg'] else '아니오'}){recency}")
        if entry["entry_confirmed"]:
            print("      ✅ 진입 조건 충족 (Put Wall 근처 저점 + FTD 확인)")
        else:
            print("      ▲ FTD는 나왔지만 Put Wall 근접 조건은 불충족 — 참고만")

    # ---------------- 2) 보유 중 관리: 주간 체크포인트 ----------------
    print("\n  [2] 보유 중 관리 (주간 체크포인트 — 매주 1회 재실행 권장)")
    if put_wall is None or call_wall is None:
        print("      Put Wall/Call Wall 값을 가져오지 못해 체크 불가")
    else:
        above_put_wall = spot > put_wall
        below_call_wall = spot < call_wall
        print(f"      Put Wall {put_wall} / Call Wall {call_wall} / 감마 체제: "
              f"{'양의 감마' if regime == 'positive' else '음의 감마' if regime else '-'}")
        print(f"      {'✅' if above_put_wall else '⚠️'} 현재가가 Put Wall {'위' if above_put_wall else '아래(이탈)'}")
        print(f"      {'✅' if below_call_wall else 'ℹ️'} 현재가가 Call Wall {'아래(여유 있음)' if below_call_wall else '위(저항 돌파)'}")

    if hull_fast[-1] is not None and hull_slow[-1] is not None:
        hull_ok = hull_fast[-1] > hull_slow[-1]
        print(f"      {'✅' if hull_ok else '⚠️'} Hull{HULL_FAST_PERIOD} "
              f"{'>' if hull_ok else '<'} Hull{HULL_SLOW_PERIOD} "
              f"({round(hull_fast[-1], 2)} / {round(hull_slow[-1], 2)})")
    if sma150[-1] is not None:
        above_sma150 = closes[-1] > sma150[-1]
        print(f"      {'✅' if above_sma150 else '⚠️'} 종가가 150일선 "
              f"{'위' if above_sma150 else '아래'} ({closes[-1]} / {round(sma150[-1], 2)})")

    # ---------------- 3) 청산: 구조적 붕괴 ----------------
    print("\n  [3] 청산 기준 (캘린더 아님 — 구조적 붕괴 신호만)")
    exit_reasons = structural_exit_signals(closes, dates, hull_fast, hull_slow, sma150)
    if not exit_reasons:
        print("      아직 구조적 붕괴 신호 없음 — 보유 지속")
    else:
        for r in exit_reasons:
            print(f"      {r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_watchlist():
    tickers = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    tickers.append(line.upper())
    return tickers


def main():
    tickers = [t.upper() for t in sys.argv[1:]] if len(sys.argv) > 1 else load_watchlist()
    if not tickers:
        print("분석할 종목이 없습니다. 인자로 티커를 넘기거나 scripts/watchlist.txt를 확인하세요.")
        return

    print("=" * 70)
    print("Put Wall + FTD 진입 / 주간 체크포인트 / Hull 데드크로스·150일선 청산")
    print(f"대상 종목: {', '.join(tickers)}")
    print("=" * 70)

    for ticker in tickers:
        try:
            analyze_one(ticker)
        except Exception as e:
            print(f"\n=== {ticker} ===")
            print(f"  분석 실패: {e}")


if __name__ == "__main__":
    main()
