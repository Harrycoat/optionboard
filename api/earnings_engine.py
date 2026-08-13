"""
earnings_engine.py
-------------------
"오늘 실적발표 + 갭/거래량 조건 만족" 종목만 추려내는 무료 스크리너.

데이터 소스:
  - Finnhub.io 무료 실적 캘린더 API (FINNHUB_API_KEY 환경변수 필요, 무료 가입)
  - Massive.com 기존 aggs 엔드포인트 (RVOL/갭 계산용, options_engine.py 재사용)

필요 환경변수:
  FINNHUB_API_KEY   (finnhub.io 무료 가입 후 발급, Vercel Environment Variables에 등록)
  MASSIVE_API_KEY   (기존과 동일)
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

# options_engine.py의 기존 함수 재사용 (같은 api/ 폴더 안에 있다고 가정)
from options_engine import fetch_daily_ohlc, MASSIVE_API_BASE, _massive_get

FINNHUB_API_BASE = "https://finnhub.io/api/v1"
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")


class FinnhubAPIError(RuntimeError):
    pass


@dataclass
class EarningsEvent:
    ticker: str
    date: str
    hour: str  # "bmo"(장전) / "amc"(장후) / "dmh"(장중) / "" (미확인)
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    eps_surprise_pct: Optional[float]
    revenue_estimate: Optional[float]
    revenue_actual: Optional[float]


@dataclass
class EarningsMover:
    ticker: str
    earnings: EarningsEvent
    gap_pct: float
    rvol: float
    today_volume: float
    avg_volume_20d: float
    today_close: float
    prev_close: float


def _finnhub_get(path: str, params: Optional[dict] = None) -> dict:
    if not FINNHUB_API_KEY:
        raise FinnhubAPIError(
            "FINNHUB_API_KEY 환경변수가 설정되지 않았습니다. "
            "finnhub.io 무료 가입 후 발급받은 키를 Vercel Environment Variables에 등록해주세요."
        )
    params = dict(params or {})
    params["token"] = FINNHUB_API_KEY
    resp = requests.get(f"{FINNHUB_API_BASE}{path}", params=params, timeout=15)
    if resp.status_code != 200:
        raise FinnhubAPIError(f"Finnhub API 오류 ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def fetch_earnings_calendar(target_date: Optional[str] = None) -> list[EarningsEvent]:
    """지정한 날짜(기본값: 오늘)에 실적발표 예정/완료된 종목 목록을 가져온다.

    Finnhub 무료 티어는 하루 단위 조회가 가장 안정적이라 from=to=target_date로 호출한다.
    """
    d = target_date or date.today().isoformat()
    data = _finnhub_get("/calendar/earnings", {"from": d, "to": d})
    raw = data.get("earningsCalendar", []) or []

    events = []
    for item in raw:
        eps_est = item.get("epsEstimate")
        eps_act = item.get("epsActual")
        surprise_pct = None
        if eps_est not in (None, 0) and eps_act is not None:
            surprise_pct = round((eps_act - eps_est) / abs(eps_est) * 100, 2)

        events.append(
            EarningsEvent(
                ticker=item.get("symbol", "").upper(),
                date=item.get("date", d),
                hour=item.get("hour", ""),
                eps_estimate=eps_est,
                eps_actual=eps_act,
                eps_surprise_pct=surprise_pct,
                revenue_estimate=item.get("revenueEstimate"),
                revenue_actual=item.get("revenueActual"),
            )
        )
    return events


def compute_gap_rvol(ticker: str, lookback_days: int = 30) -> Optional[dict]:
    """최근 lookback_days 일봉으로 RVOL(오늘 거래량 / 최근 20일 평균)과 갭%(오늘 시가 vs 전일 종가)를 계산한다.

    데이터 부족(신규 상장 등)이면 None을 반환한다.
    """
    bars, _debug = fetch_daily_ohlc(ticker, lookback_days=lookback_days)
    if len(bars) < 21:
        return None

    today_bar = bars[-1]
    prior_bars = bars[-21:-1]  # 오늘 제외 최근 20거래일

    avg_volume_20d = sum(b["volume"] for b in prior_bars) / len(prior_bars)
    if avg_volume_20d <= 0:
        return None

    today_volume = today_bar["volume"]
    rvol = today_volume / avg_volume_20d

    prev_close = prior_bars[-1]["close"]
    today_open = today_bar["open"]
    gap_pct = (today_open - prev_close) / prev_close * 100 if prev_close else 0.0

    return {
        "gap_pct": round(gap_pct, 2),
        "rvol": round(rvol, 2),
        "today_volume": today_volume,
        "avg_volume_20d": round(avg_volume_20d, 0),
        "today_close": today_bar["close"],
        "prev_close": prev_close,
    }


def scan_earnings_movers(
    target_date: Optional[str] = None,
    min_gap_pct: float = 3.0,
    min_rvol: float = 2.0,
    max_tickers_to_check: int = 60,
) -> list[EarningsMover]:
    """오늘 실적발표한 종목 중, 갭%/RVOL 조건을 만족하는 종목만 추려낸다.

    Finnhub 무료 티어는 실적 캘린더에 미국 상장 종목이 매우 많이 나오므로
    (하루 200~400개), 전부 다 Massive API로 조회하면 rate limit에 걸리기 쉽다.
    그래서 max_tickers_to_check로 상한을 두고, 필요하면 해리님의 기존
    active_universe.txt / leaders_watchlist.txt 같은 관심종목 리스트와
    교집합만 검사하는 방식으로 좁혀서 쓰는 것을 권장한다.
    """
    events = fetch_earnings_calendar(target_date)
    events = events[:max_tickers_to_check]

    movers = []
    for ev in events:
        if not ev.ticker:
            continue
        try:
            metrics = compute_gap_rvol(ev.ticker)
        except Exception:
            # 개별 종목 조회 실패는 건너뛴다 (상장폐지, 티커 오류 등)
            continue
        if metrics is None:
            continue
        if metrics["gap_pct"] >= min_gap_pct and metrics["rvol"] >= min_rvol:
            movers.append(
                EarningsMover(
                    ticker=ev.ticker,
                    earnings=ev,
                    gap_pct=metrics["gap_pct"],
                    rvol=metrics["rvol"],
                    today_volume=metrics["today_volume"],
                    avg_volume_20d=metrics["avg_volume_20d"],
                    today_close=metrics["today_close"],
                    prev_close=metrics["prev_close"],
                )
            )

    # 갭% 큰 순서로 정렬
    movers.sort(key=lambda m: m.gap_pct, reverse=True)
    return movers


def scan_earnings_movers_from_watchlist(
    watchlist_tickers: list[str],
    target_date: Optional[str] = None,
    min_gap_pct: float = 3.0,
    min_rvol: float = 2.0,
) -> list[EarningsMover]:
    """전체 실적 캘린더 대신, 해리님의 관심종목 리스트(예: leaders_watchlist.txt에서
    읽은 티커 목록)와 오늘 실적발표 종목의 교집합만 검사한다.

    Massive API 호출 횟수를 크게 줄일 수 있어서 rate limit(429) 걱정 없이
    매일 아침 돌리기에 더 안전하다.
    """
    events = {ev.ticker: ev for ev in fetch_earnings_calendar(target_date)}
    watchlist_set = {t.upper().strip() for t in watchlist_tickers}
    matched_tickers = watchlist_set & set(events.keys())

    movers = []
    for ticker in matched_tickers:
        try:
            metrics = compute_gap_rvol(ticker)
        except Exception:
            continue
        if metrics is None:
            continue
        if metrics["gap_pct"] >= min_gap_pct and metrics["rvol"] >= min_rvol:
            ev = events[ticker]
            movers.append(
                EarningsMover(
                    ticker=ticker,
                    earnings=ev,
                    gap_pct=metrics["gap_pct"],
                    rvol=metrics["rvol"],
                    today_volume=metrics["today_volume"],
                    avg_volume_20d=metrics["avg_volume_20d"],
                    today_close=metrics["today_close"],
                    prev_close=metrics["prev_close"],
                )
            )

    movers.sort(key=lambda m: m.gap_pct, reverse=True)
    return movers


def _mover_to_dict(m: EarningsMover) -> dict:
    return {
        "ticker": m.ticker,
        "earnings_date": m.earnings.date,
        "earnings_hour": m.earnings.hour,  # bmo/amc/dmh
        "eps_estimate": m.earnings.eps_estimate,
        "eps_actual": m.earnings.eps_actual,
        "eps_surprise_pct": m.earnings.eps_surprise_pct,
        "gap_pct": m.gap_pct,
        "rvol": m.rvol,
        "today_volume": m.today_volume,
        "avg_volume_20d": m.avg_volume_20d,
        "today_close": m.today_close,
        "prev_close": m.prev_close,
    }


if __name__ == "__main__":
    import json
    import sys

    # 사용 예: python earnings_engine.py           -> 오늘 전체 실적발표 종목 중 조건 만족 스캔
    #         python earnings_engine.py AAPL,NVDA,ANET  -> 관심종목 리스트로 좁혀서 스캔
    if len(sys.argv) > 1:
        tickers = [t.strip() for t in sys.argv[1].split(",")]
        results = scan_earnings_movers_from_watchlist(tickers)
    else:
        results = scan_earnings_movers()

    print(json.dumps([_mover_to_dict(m) for m in results], indent=2, ensure_ascii=False))