"""
scripts/generate_leaders_report.py

leaders_watchlist.txt를 읽어서 카테고리별(WAVE1/WAVE2/SPECULATIVE) 종목의
GEX 배지(Call Wall 근접도, Gamma Flip 레짐) + 현재가/등락률/거래량을 계산하고
public/leaders_report.json 으로 저장합니다.

- GEX 배지: options_engine.analyze_ticker() 재사용 (Massive.com API, 실측 gamma)
- 가격/등락률/거래량: Massive.com의 전일 일봉(prev bar) 엔드포인트 사용

daily_update.py 바로 다음에 같은 크론 잡 안에서 실행되며, Massive API
호출이 몰려서 분당 호출 제한에 걸리는 경우가 있어 다음과 같이 대응한다:
  1) 시작 전 대기 (daily_update.py의 호출 버스트가 가라앉을 시간)
  2) 종목 사이사이 딜레이
  3) GEX 계산 실패 시 최대 2회 재시도 (재시도마다 대기시간 증가)
  4) 가격/거래량(일봉) 조회 실패 시에도 별도로 재시도

daily_update.py와 동일한 크론(.github/workflows/daily-update.yml)에서
이어서 호출하면 매일 자동 갱신됩니다.

---
[Top10 Gamma Flip 스캐너 — 2단계 구조]

문제: S&P500+나스닥100 전체(520종목)를 매일 스캔하면 종목당 현재가(prev) 폴백
호출까지 겹쳐서 1~2시간씩 걸린다.

해결: 스캔을 2단계로 나눈다.
  [주 1회, 월요일] 전체 520종목을 "유동성(옵션 미결제약정 OI 합계)" 기준으로만
                   스캔한다. 현재가 조회가 필요 없어 종목당 API 호출이 1번뿐이라
                   훨씬 빠르고 429 에러도 적다. 상위 100개를 추려서
                   active_universe.txt에 저장한다.
  [매일]           active_universe.txt(100종목)만 스캔해서 Top10 Gamma Flip을
                   계산한다. 100종목이라 현재가 조회를 포함해도 훨씬 빠르다.

[Unusual Options Activity 스캐너 — "이상 옵션 거래"]

active_universe.txt(100종목)를 그대로 재사용해서(추가 유니버스 재계산 없음),
options_engine.fetch_oi_volume_snapshot()(이미 OI 변화 추적 기능에서 쓰던
경량 스냅샷 — 스트라이크별 call/put OI + 당일 거래량 포함)를 종목당 1번씩만
호출해서, 거래량이 기존 OI 대비 유난히 큰 계약(barchart.com의 "Unusual
Options Activity" 스크리너와 같은 개념)들을 Vol/OI 비율 내림차순으로 뽑아낸다.
"""
import json
import os
import sys
import time
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import (
    analyze_ticker,
    quick_gamma_flip,
    rank_by_liquidity,
    fetch_oi_volume_snapshot,
    fetch_daily_ohlc,
    MASSIVE_API_BASE,
    MASSIVE_API_KEY,
)
WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "leaders_watchlist.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")
UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "sp500_nasdaq100_universe.txt")
ACTIVE_UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "active_universe.txt")

CATEGORY_KEYS = {"WAVE1": "wave1", "WAVE2": "wave2", "SPECULATIVE": "speculative"}

STARTUP_DELAY_SECONDS = 20
PER_TICKER_DELAY_SECONDS = 4
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = [8, 15]

UNIVERSE_PER_TICKER_DELAY_SECONDS = 0.5
UNIVERSE_MAX_RETRIES = 1
UNIVERSE_RETRY_BACKOFF_SECONDS = [5]

LIQUIDITY_SCAN_WEEKDAY = 0
ACTIVE_UNIVERSE_TOP_N = 100
LIQUIDITY_PER_TICKER_DELAY_SECONDS = 0.3
LIQUIDITY_MAX_RETRIES = 1
LIQUIDITY_RETRY_BACKOFF_SECONDS = [5]

UNUSUAL_OPTIONS_MIN_VOLUME = 300
UNUSUAL_OPTIONS_MIN_RATIO = 1.0
UNUSUAL_OPTIONS_TOP_N = 15
UNUSUAL_OPTIONS_MAX_PER_TICKER = 3  # 한 종목(예: 신규 옵션 상장)이 결과를 독점하지 않도록 제한
WALL_HISTORY_LIMIT = 20
WALL_SHIFT_TOP_N = 10
CALL_WALL_SCAN_TOP_N = 15
CALL_WALL_BREAK_BUFFER_PCT = 0.3
CALL_WALL_PREP_DISTANCE_PCT = 1.0
CALL_WALL_MIN_RVOL = 1.5

_QUICK_FLIP_CACHE = {}
_ACTIVE_UNIVERSE_CACHE = None


def parse_watchlist(path):
    categories = {"wave1": [], "wave2": [], "speculative": []}
    current = None
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                header = line.strip("[]").upper()
                current = CATEGORY_KEYS.get(header)
                continue
            if current is None:
                continue
            parts = [p.strip() for p in line.split(",")]
            ticker = parts[0]
            sector = parts[1] if len(parts) > 1 else ""
            categories[current].append({"ticker": ticker, "sector": sector})
    return categories


def fetch_daily_bar_once(ticker: str):
    if not MASSIVE_API_KEY:
        return None
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/prev"
    resp = requests.get(url, params={"apiKey": MASSIVE_API_KEY}, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    data = resp.json()
    results = data.get("results") or []
    if not results:
        raise RuntimeError("결과 없음")
    r = results[0]
    return {"open": r.get("o"), "close": r.get("c"), "volume": r.get("v")}


def fetch_daily_bar(ticker: str):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fetch_daily_bar_once(ticker)
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[attempt]
                print(f"    일봉 조회 실패 ({ticker}, {attempt+1}차): {e} → {wait}초 후 재시도")
                time.sleep(wait)
            else:
                print(f"    일봉 조회 최종 실패 ({ticker}): {e}")
                return None


def try_analyze(ticker: str):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return analyze_ticker(ticker), None
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[attempt]
                print(f"    GEX 계산 실패 ({ticker}, {attempt+1}차): {e} → {wait}초 후 재시도")
                time.sleep(wait)
            else:
                return None, str(e)


def build_badge(ticker: str, sector: str) -> dict:
    badge = {
        "ticker": ticker,
        "sector": sector,
        "spot": None,
        "call_wall": None,
        "put_wall": None,
        "call_wall_distance_pct": None,
        "gamma_flip": None,
        "gamma_regime": None,
        "price_change_pct": None,
        "volume": None,
        "stage": None,
        "stage_label": None,
        "vex_total": None,
        "cex_total": None,
        "vanna_charm_expiry_days": None,
        "status": "ok",
    }
    result, err = try_analyze(ticker)
    if result:
        spot = result.get("spot")
        call_wall = result.get("call_wall")
        badge["spot"] = round(spot, 2) if spot is not None else None
        badge["call_wall"] = call_wall
        badge["put_wall"] = result.get("put_wall")
        badge["gamma_flip"] = result.get("gamma_flip")
        badge["gamma_regime"] = result.get("regime")
        badge["stage"] = result.get("stage")
        badge["stage_label"] = result.get("stage_label")
        badge["vex_total"] = result.get("vex_total")
        badge["cex_total"] = result.get("cex_total")
        badge["vanna_charm_expiry_days"] = result.get("vanna_charm_expiry_days")
        if call_wall is not None and spot:
            badge["call_wall_distance_pct"] = round((call_wall - spot) / spot * 100, 2)
    else:
        badge["status"] = f"error: {err}"
    bar = fetch_daily_bar(ticker)
    if bar:
        badge["volume"] = bar.get("volume")
        o, c = bar.get("open"), bar.get("close")
        if o and c:
            badge["price_change_pct"] = round((c - o) / o * 100, 2)
        if badge["spot"] is None and c:
            badge["spot"] = round(c, 2)
    return badge


# ---------------------------------------------------------------------------
# 급등주 스캔 대상 유니버스: 위키피디아 "S&P500 구성종목" / "나스닥100 구성종목"
# 문서에서 매주 자동으로 긁어온다.
# ---------------------------------------------------------------------------
WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
WIKI_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; gexoption-universe-bot/1.0)"
}


def _clean_ticker(raw) -> str | None:
    """위키피디아 표기(BRK.B 등)를 브로커/API 표준 표기(BRK-B)로 정규화한다."""
    import re
    t = str(raw).strip().upper().replace(".", "-")
    return t if re.match(r"^[A-Z0-9\-]{1,6}$", t) else None


def fetch_universe_from_wikipedia() -> list:
    """위키피디아에서 S&P500 + 나스닥100 구성종목 티커를 실시간으로 가져온다."""
    import io
    import pandas as pd

    tickers: set = set()

    resp = requests.get(WIKI_SP500_URL, headers=WIKI_REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    sp500_tables = pd.read_html(io.StringIO(resp.text))
    sp500_df = None
    for tbl in sp500_tables:
        cols = set(str(c) for c in tbl.columns)
        if {"Symbol", "Security"}.issubset(cols):
            sp500_df = tbl
            break
    if sp500_df is None:
        raise ValueError("S&P500 위키피디아 표에서 Symbol/Security 컬럼을 찾지 못했습니다")
    for t in sp500_df["Symbol"]:
        cleaned = _clean_ticker(t)
        if cleaned:
            tickers.add(cleaned)

    resp2 = requests.get(WIKI_NASDAQ100_URL, headers=WIKI_REQUEST_HEADERS, timeout=20)
    resp2.raise_for_status()
    ndx_tables = pd.read_html(io.StringIO(resp2.text))
    ndx_df = None
    for tbl in ndx_tables:
        cols = set(str(c) for c in tbl.columns)
        if {"Ticker", "Company"}.issubset(cols):
            ndx_df = tbl
            break
    if ndx_df is None:
        raise ValueError("나스닥100 위키피디아 표에서 Ticker/Company 컬럼을 찾지 못했습니다")
    for t in ndx_df["Ticker"]:
        cleaned = _clean_ticker(t)
        if cleaned:
            tickers.add(cleaned)

    return sorted(tickers)


def load_universe(path: str) -> list:
    tickers = []
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line)
    return tickers


def load_universe_preferring_wiki(fallback_path: str = UNIVERSE_PATH) -> list:
    try:
        tickers = fetch_universe_from_wikipedia()
        if tickers:
            print(f"위키피디아에서 유니버스 로드 성공: {len(tickers)}개 종목")
            return tickers
        print("위키피디아 응답이 비어있어 로컬 파일로 폴백합니다.")
    except Exception as e:
        print(f"위키피디아 유니버스 로드 실패({e}), 로컬 파일로 폴백합니다.")
    return load_universe(fallback_path)


def compute_flip_distance_pct(spot, gamma_flip):
    if spot is None or gamma_flip is None or spot == 0:
        return None
    return abs(spot - gamma_flip) / spot * 100


def try_quick_flip(ticker: str):
    if ticker in _QUICK_FLIP_CACHE:
        return _QUICK_FLIP_CACHE[ticker], None
    for attempt in range(UNIVERSE_MAX_RETRIES + 1):
        try:
            result = quick_gamma_flip(ticker)
            _QUICK_FLIP_CACHE[ticker] = result
            return result, None
        except Exception as e:
            if attempt < UNIVERSE_MAX_RETRIES:
                wait = UNIVERSE_RETRY_BACKOFF_SECONDS[attempt]
                time.sleep(wait)
            else:
                return None, str(e)


def try_rank_liquidity(ticker: str):
    for attempt in range(LIQUIDITY_MAX_RETRIES + 1):
        try:
            return rank_by_liquidity(ticker), None
        except Exception as e:
            if attempt < LIQUIDITY_MAX_RETRIES:
                wait = LIQUIDITY_RETRY_BACKOFF_SECONDS[attempt]
                time.sleep(wait)
            else:
                return None, str(e)


def try_oi_volume_snapshot(ticker: str):
    for attempt in range(UNIVERSE_MAX_RETRIES + 1):
        try:
            return fetch_oi_volume_snapshot(ticker, max_expiries=2), None
        except Exception as e:
            if attempt < UNIVERSE_MAX_RETRIES:
                wait = UNIVERSE_RETRY_BACKOFF_SECONDS[attempt]
                time.sleep(wait)
            else:
                return None, str(e)


def build_active_universe(
    universe_path: str = UNIVERSE_PATH,
    output_path: str = ACTIVE_UNIVERSE_PATH,
    top_n: int = ACTIVE_UNIVERSE_TOP_N,
) -> list:
    tickers = load_universe_preferring_wiki(universe_path)
    print(f"\n[주간] 유동성 스캔 시작: {len(tickers)}개 종목")
    ranked = []
    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")
        result, err = try_rank_liquidity(ticker)
        if result and result.get("total_oi", 0) > 0:
            ranked.append(result)
        time.sleep(LIQUIDITY_PER_TICKER_DELAY_SECONDS)
    ranked.sort(key=lambda x: x["total_oi"], reverse=True)
    top_tickers = [r["ticker"] for r in ranked[:top_n]]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# scripts/active_universe.txt\n")
        f.write(f"# 유동성(OI) 기준 상위 {top_n}개 — 매주 월요일 자동 갱신\n")
        f.write(f"# 생성: {datetime.now(timezone.utc).isoformat()}\n")
        for t in top_tickers:
            f.write(f"{t}\n")
    print(f"[주간] 유동성 스캔 완료: {len(ranked)}개 유효 / 상위 {len(top_tickers)}개 저장 → {output_path}")
    return top_tickers


def load_or_build_active_universe() -> list:
    global _ACTIVE_UNIVERSE_CACHE
    if _ACTIVE_UNIVERSE_CACHE is not None:
        return list(_ACTIVE_UNIVERSE_CACHE)
    is_scan_day = datetime.now(timezone.utc).weekday() == LIQUIDITY_SCAN_WEEKDAY
    file_exists = os.path.exists(ACTIVE_UNIVERSE_PATH)
    if is_scan_day or not file_exists:
        reason = "월요일" if is_scan_day else "active_universe.txt 없음"
        print(f"\n유동성 재스캔 조건 충족 ({reason}) — 전체 유니버스 스캔 실행")
        _ACTIVE_UNIVERSE_CACHE = build_active_universe()
    else:
        print(f"\n기존 active_universe.txt 재사용 (다음 갱신: 월요일)")
        _ACTIVE_UNIVERSE_CACHE = load_universe(ACTIVE_UNIVERSE_PATH)
    return list(_ACTIVE_UNIVERSE_CACHE)


def build_top10_gamma_flip(top_n: int = 10) -> list:
    tickers = load_or_build_active_universe()
    print(f"\nGamma Flip 스캐너: {len(tickers)}개 종목 스캔 시작 (경량 모드)")
    candidates = []
    skipped = 0
    for i, ticker in enumerate(tickers, 1):
        if i % 20 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")
        result, err = try_quick_flip(ticker)
        if result:
            spot = result.get("spot")
            gamma_flip = result.get("gamma_flip")
            dist_pct = compute_flip_distance_pct(spot, gamma_flip)
            if dist_pct is not None:
                candidates.append({
                    "ticker": ticker,
                    "spot": round(spot, 2) if spot is not None else None,
                    "gamma_flip": gamma_flip,
                    "gamma_regime": result.get("regime"),
                    "flip_distance_pct": round(dist_pct, 2),
                })
            else:
                skipped += 1
        else:
            skipped += 1
        time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
    candidates.sort(key=lambda x: x["flip_distance_pct"])
    top10 = candidates[:top_n]
    print(
        f"Gamma Flip 스캐너 완료: 유효 {len(candidates)}개 / "
        f"스킵 {skipped}개 / Top {top_n} 추출"
    )
    return top10


def build_top_gainers(top_n: int = 10, min_price: float = 5.0, min_volume: int = 300_000) -> list:
    tickers = load_or_build_active_universe()
    print(f"\nTop Gainers 스캐너: {len(tickers)}개 종목 스캔 시작 (경량 모드)")
    candidates = []
    skipped = 0
    for i, ticker in enumerate(tickers, 1):
        if i % 20 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")
        bar = fetch_daily_bar(ticker)
        if not bar:
            skipped += 1
            time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
            continue
        o, c, v = bar.get("open"), bar.get("close"), bar.get("volume")
        if not o or not c:
            skipped += 1
            time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
            continue
        if c < min_price or (v is not None and v < min_volume):
            skipped += 1
            time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
            continue
        pct = round((c - o) / o * 100, 2)
        candidates.append({
            "ticker": ticker,
            "spot": round(c, 2),
            "price_change_pct": pct,
            "volume": v,
        })
        time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
    candidates.sort(key=lambda x: x["price_change_pct"], reverse=True)
    top = candidates[:top_n]
    print(f"  1단계 완료: 유효 {len(candidates)}개 / 필터제외 {skipped}개")
    print(f"  2단계: 상위 {len(top)}개 GEX 보강 계산 중...")
    for entry in top:
        result, err = try_analyze(entry["ticker"])
        if result:
            entry["gamma_flip"] = result.get("gamma_flip")
            entry["gamma_regime"] = result.get("regime")
            entry["call_wall"] = result.get("call_wall")
            entry["put_wall"] = result.get("put_wall")
            entry["stage"] = result.get("stage")
            entry["stage_label"] = result.get("stage_label")
        else:
            entry["gamma_flip"] = None
            entry["gamma_regime"] = None
            entry["call_wall"] = None
            entry["put_wall"] = None
            entry["stage"] = None
            entry["stage_label"] = None
        time.sleep(PER_TICKER_DELAY_SECONDS)
    print(f"Top Gainers 스캐너 완료: Top {len(top)} 추출")
    return top


def _sma(values: list[float], period: int):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values: list[float], period: int):
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for price in values[period:]:
        value = price * multiplier + value * (1 - multiplier)
    return value


def _intraday_volume_fraction(now_utc=None) -> float:
    """미 동부 장중 경과 비율. 장 시작 직후 과대평가 방지를 위해 최소 10%로 둔다."""
    now_et = (now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    market_open = datetime.combine(now_et.date(), dt_time(9, 30), tzinfo=now_et.tzinfo)
    market_close = datetime.combine(now_et.date(), dt_time(16, 0), tzinfo=now_et.tzinfo)
    if now_et <= market_open:
        return 0.10
    if now_et >= market_close:
        return 1.0
    elapsed = (now_et - market_open).total_seconds()
    return max(0.10, min(1.0, elapsed / (6.5 * 60 * 60)))


def _previous_breakout_map(previous_report: dict) -> dict:
    return {
        row.get("ticker"): row
        for row in previous_report.get("call_wall_breakouts", [])
        if row.get("ticker")
    }


def build_call_wall_breakout_scan(tickers: list, previous_report: dict) -> list:
    """Call Wall 돌파를 주식 거래량과 추세선으로 확인하는 수동 스윙 후보 스캔."""
    print(f"\nCall Wall 거래량 돌파 스캐너: {len(tickers)}개 종목 스캔 시작")
    previous_map = _previous_breakout_map(previous_report)
    volume_fraction = _intraday_volume_fraction()
    candidates = []

    for i, ticker in enumerate(tickers, 1):
        if i % 20 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")
        gex, err = try_quick_flip(ticker)
        if not gex or gex.get("call_wall") is None or gex.get("spot") is None:
            continue

        bars, _debug = fetch_daily_ohlc(ticker, lookback_days=330)
        if len(bars) < 21:
            continue
        closes = [float(row["close"]) for row in bars]
        current_bar = bars[-1]
        current_volume = float(current_bar.get("volume") or 0)
        today_et = datetime.now(timezone.utc).astimezone(
            ZoneInfo("America/New_York")
        ).date().isoformat()
        volume_is_current_day = current_bar.get("time") == today_et
        applied_volume_fraction = volume_fraction if volume_is_current_day else 1.0
        historical_volumes = [
            float(row.get("volume") or 0) for row in bars[-21:-1]
            if row.get("volume") is not None
        ]
        avg_volume_20 = (
            sum(historical_volumes) / len(historical_volumes)
            if historical_volumes else None
        )
        expected_volume = avg_volume_20 * applied_volume_fraction if avg_volume_20 else None
        relative_volume = current_volume / expected_volume if expected_volume else None

        spot = float(gex["spot"])
        call_wall = float(gex["call_wall"])
        wall_distance_pct = (spot - call_wall) / call_wall * 100
        above_wall = spot > call_wall
        buffered_breakout = wall_distance_pct >= CALL_WALL_BREAK_BUFFER_PCT
        volume_confirmed = bool(
            volume_is_current_day
            and not gex.get("is_stale_price")
            and relative_volume is not None
            and relative_volume >= CALL_WALL_MIN_RVOL
        )

        previous = previous_map.get(ticker) or {}
        previously_above = bool(previous.get("above_call_wall"))
        if previously_above and not above_wall:
            signal_type, signal_label = "failed", "⚠️ 돌파 실패"
        elif previously_above and above_wall:
            signal_type, signal_label = "holding", "✅ 돌파 유지"
        elif buffered_breakout and volume_confirmed:
            signal_type, signal_label = "confirmed", "🚀 거래량 돌파 확인"
        elif above_wall:
            signal_type, signal_label = "attempt", "🟡 돌파 시도"
        elif wall_distance_pct >= -CALL_WALL_PREP_DISTANCE_PCT:
            signal_type, signal_label = "preparing", "👀 돌파 준비"
        else:
            continue

        ema21 = _ema(closes, 21)
        ma50 = _sma(closes, 50)
        ma200 = _sma(closes, 200)
        trend_confirmed = bool(
            ema21 is not None and ma50 is not None and ma200 is not None
            and spot > ema21 and spot > ma50 and ma50 > ma200
        )
        gamma_bonus = 15 if gex.get("regime") == "negative" else 0
        signal_rank = {
            "confirmed": 5, "holding": 4, "attempt": 3, "preparing": 2, "failed": 1
        }[signal_type]
        score = (
            signal_rank * 100
            + min(relative_volume or 0, 5) * 10
            + (20 if trend_confirmed else 0)
            + gamma_bonus
            + max(min(wall_distance_pct, 5), -5)
        )
        candidates.append({
            "ticker": ticker,
            "spot": round(spot, 2),
            "call_wall": call_wall,
            "wall_distance_pct": round(wall_distance_pct, 2),
            "above_call_wall": above_wall,
            "signal_type": signal_type,
            "signal_label": signal_label,
            "relative_volume": round(relative_volume, 2) if relative_volume is not None else None,
            "current_volume": round(current_volume),
            "average_volume_20": round(avg_volume_20) if avg_volume_20 is not None else None,
            "volume_fraction": round(applied_volume_fraction, 3),
            "volume_as_of": current_bar.get("time"),
            "volume_is_current_day": volume_is_current_day,
            "volume_confirmed": volume_confirmed,
            "gamma_regime": gex.get("regime"),
            "ema21": round(ema21, 2) if ema21 is not None else None,
            "ma50": round(ma50, 2) if ma50 is not None else None,
            "ma200": round(ma200, 2) if ma200 is not None else None,
            "trend_confirmed": trend_confirmed,
            "score": round(score, 2),
        })
        time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)

    candidates.sort(key=lambda row: (-row["score"], row["ticker"]))
    result = candidates[:CALL_WALL_SCAN_TOP_N]
    print(
        f"Call Wall 돌파 스캐너 완료: 후보 {len(candidates)}개 / "
        f"거래량 확인 {sum(1 for row in candidates if row['signal_type'] == 'confirmed')}개 / "
        f"Top {len(result)} 표시"
    )
    return result


def build_unusual_options_activity(
    tickers: list,
    top_n: int = UNUSUAL_OPTIONS_TOP_N,
    min_volume: int = UNUSUAL_OPTIONS_MIN_VOLUME,
    min_ratio: float = UNUSUAL_OPTIONS_MIN_RATIO,
) -> list:
    """스트라이크별 Vol/OI(거래량 대비 미결제약정 비율)이 가장 높은 계약들을 뽑아낸다.

    barchart.com의 "Unusual Options Activity" 스크리너와 같은 개념 — 오늘 거래량이
    기존 OI 대비 유난히 크게 튄 계약은 "새로운 대규모 포지션이 오늘 생겼을 가능성"을
    시사한다. active_universe(유동성 상위 종목군, dev_reentry 스캐너와 동일 리스트)를
    그대로 재사용해서 추가 유니버스 재계산 없이 이어서 스캔한다.
    """
    print(f"\nUnusual Options Activity 스캐너: {len(tickers)}개 종목 스캔 시작 (경량 모드)")
    candidates = []
    skipped = 0
    for i, ticker in enumerate(tickers, 1):
        if i % 20 == 0 or i == 1:
            print(f"  진행: {i}/{len(tickers)} ({ticker})")
        snap, err = try_oi_volume_snapshot(ticker)
        if not snap:
            skipped += 1
            time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)
            continue
        spot = snap.get("spot")
        for row in snap.get("strikes", []):
            strike = row.get("strike")
            for side, oi_key, vol_key in (("call", "call_oi", "call_volume"), ("put", "put_oi", "put_volume")):
                oi = row.get(oi_key) or 0
                vol = row.get(vol_key) or 0
                if vol < min_volume:
                    continue
                if oi > 0:
                    ratio = vol / oi
                    if ratio < min_ratio:
                        continue
                else:
                    ratio = None  # OI 0인데 거래량 있음 = 오늘 신규 생성된 계약(최상위 이상신호)
                candidates.append({
                    "ticker": ticker,
                    "spot": round(spot, 2) if spot is not None else None,
                    "strike": strike,
                    "side": side,
                    "volume": round(vol),
                    "oi": round(oi),
                    "vol_oi_ratio": round(ratio, 2) if ratio is not None else None,
                    "is_new_contract": oi <= 0,
                })
        time.sleep(UNIVERSE_PER_TICKER_DELAY_SECONDS)

    # 실제 Vol/OI 비율이 있는 후보가 항상 "신규 계약"(비율 없음)보다 위로 오도록 정렬한다.
    # (이전 버전은 신규 계약을 무한대로 취급해서 진짜 이상신호를 다 밀어냈던 버그가 있었음)
    candidates.sort(
        key=lambda c: c["vol_oi_ratio"] if c["vol_oi_ratio"] is not None else -1,
        reverse=True,
    )

    # 한 종목(예: 오늘 새 만기 옵션이 무더기로 상장된 경우)이 상위권을 독점하지 않도록
    # 티커당 최대 개수를 제한해서 다양한 종목이 섞여 나오게 한다.
    top = []
    per_ticker_count = {}
    for c in candidates:
        cnt = per_ticker_count.get(c["ticker"], 0)
        if cnt >= UNUSUAL_OPTIONS_MAX_PER_TICKER:
            continue
        top.append(c)
        per_ticker_count[c["ticker"]] = cnt + 1
        if len(top) >= top_n:
            break

    print(
        f"Unusual Options Activity 스캐너 완료: 후보 {len(candidates)}개 / "
        f"스킵 {skipped}개 / Top {len(top)} 추출"
    )
    return top


def build_gamma_squeeze_candidates(categories_report: dict) -> list:
    candidates = []
    for cat_key, entries in categories_report.items():
        for e in entries:
            if e.get("status") != "ok":
                continue
            if e.get("gamma_regime") != "negative":
                continue
            pct = e.get("price_change_pct")
            if pct is None or pct <= 0:
                continue
            candidates.append({
                "ticker": e["ticker"],
                "sector": e.get("sector"),
                "category": cat_key,
                "spot": e.get("spot"),
                "price_change_pct": pct,
                "gamma_flip": e.get("gamma_flip"),
                "call_wall": e.get("call_wall"),
                "call_wall_distance_pct": e.get("call_wall_distance_pct"),
            })
    candidates.sort(key=lambda x: x["price_change_pct"], reverse=True)
    return candidates


def build_vanna_squeeze_candidates(categories_report: dict, top_n: int = 8) -> list:
    candidates = []
    for cat_key, entries in categories_report.items():
        for e in entries:
            if e.get("status") != "ok":
                continue
            vex = e.get("vex_total")
            pct = e.get("price_change_pct")
            if vex is None or pct is None or pct <= 0:
                continue
            candidates.append({
                "ticker": e["ticker"],
                "sector": e.get("sector"),
                "category": cat_key,
                "spot": e.get("spot"),
                "price_change_pct": pct,
                "vex_total": vex,
            })
    candidates.sort(key=lambda x: abs(x["vex_total"]), reverse=True)
    return candidates[:top_n]


def build_charm_squeeze_candidates(categories_report: dict, max_days: int = 5, top_n: int = 8) -> list:
    candidates = []
    for cat_key, entries in categories_report.items():
        for e in entries:
            if e.get("status") != "ok":
                continue
            cex = e.get("cex_total")
            days = e.get("vanna_charm_expiry_days")
            if cex is None or days is None:
                continue
            if days < 0 or days > max_days:
                continue
            candidates.append({
                "ticker": e["ticker"],
                "sector": e.get("sector"),
                "category": cat_key,
                "spot": e.get("spot"),
                "days_to_expiry": days,
                "cex_total": cex,
                "gamma_flip": e.get("gamma_flip"),
            })
    candidates.sort(key=lambda x: abs(x["cex_total"]), reverse=True)
    return candidates[:top_n]


def _load_previous_report() -> dict:
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _flatten_wall_snapshots(categories_report: dict, captured_at: str) -> dict:
    snapshots = {}
    for category, entries in categories_report.items():
        for entry in entries:
            if entry.get("status") != "ok" or entry.get("spot") is None:
                continue
            snapshots[entry["ticker"]] = {
                "captured_at": captured_at,
                "category": category,
                "sector": entry.get("sector"),
                "spot": entry.get("spot"),
                "call_wall": entry.get("call_wall"),
                "put_wall": entry.get("put_wall"),
                "gamma_flip": entry.get("gamma_flip"),
                "gamma_regime": entry.get("gamma_regime"),
            }
    return snapshots


def _number_delta(current, previous):
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 2)


def _pct_of_spot(value, spot):
    if value is None or not spot:
        return None
    return round(float(value) / float(spot) * 100, 2)


def _wall_shift_label(call_delta, put_delta, flip_delta, spot_delta, has_previous):
    if not has_previous:
        return "기준값 저장", "baseline"
    call_delta = call_delta or 0
    put_delta = put_delta or 0
    spot_delta = spot_delta or 0
    if call_delta < 0 and put_delta > 0:
        return "변동 구간 압축", "squeeze"
    if call_delta > 0 and put_delta < 0:
        return "상하단 범위 확대", "expansion"
    if put_delta > 0 and call_delta >= 0:
        if spot_delta > 0:
            return "상승 이동 확인", "bullish"
        return "상승 구조·가격 대기", "watch_up"
    if put_delta < 0 and call_delta <= 0:
        if spot_delta < 0:
            return "하락 이동 확인", "bearish"
        return "하락 구조·가격 버팀", "watch_down"
    if call_delta > 0:
        if spot_delta > 0:
            return "Call Wall 상승 확인", "bullish"
        return "Call Wall 상승·가격 대기", "upside"
    if call_delta < 0:
        if spot_delta < 0:
            return "Call Wall 하락 확인", "bearish"
        return "Call Wall 하락·가격 버팀", "downside"
    return "주요 Wall 유지", "stable"


def build_wall_shift_radar(categories_report: dict, previous_report: dict, captured_at: str):
    """직전 리포트와 현재 리포트의 Wall 이동을 비교하고 이력을 보존한다."""
    history = previous_report.get("wall_history") or {}
    history = {
        ticker: rows[-WALL_HISTORY_LIMIT:]
        for ticker, rows in history.items()
        if isinstance(rows, list)
    }

    # Wall Shift 첫 배포 시 기존 리포트의 카테고리 값을 직전 기준으로 사용한다.
    if not history and previous_report.get("categories"):
        previous_at = previous_report.get("generated_at") or "previous-report"
        for ticker, snapshot in _flatten_wall_snapshots(
            previous_report["categories"], previous_at
        ).items():
            history[ticker] = [snapshot]

    current = _flatten_wall_snapshots(categories_report, captured_at)
    radar = []
    for ticker, snapshot in current.items():
        rows = history.setdefault(ticker, [])
        rows = [row for row in rows if row.get("captured_at") != captured_at]
        rows.append(snapshot)
        history[ticker] = rows[-WALL_HISTORY_LIMIT:]

        previous = history[ticker][-2] if len(history[ticker]) >= 2 else None
        call_delta = _number_delta(snapshot.get("call_wall"), previous and previous.get("call_wall"))
        put_delta = _number_delta(snapshot.get("put_wall"), previous and previous.get("put_wall"))
        flip_delta = _number_delta(snapshot.get("gamma_flip"), previous and previous.get("gamma_flip"))
        spot_delta = _number_delta(snapshot.get("spot"), previous and previous.get("spot"))
        previous_spot = previous and previous.get("spot")
        spot_change_pct = _pct_of_spot(spot_delta, previous_spot)
        label, shift_type = _wall_shift_label(
            call_delta, put_delta, flip_delta, spot_delta, previous is not None
        )

        # 상승 후보 전용: Put Wall(지지)이 올라왔거나 Call Wall(저항)이
        # 위로 이동한 종목만 보여준다. 유지/하락 및 Gamma Flip 단독 변화는 제외한다.
        bullish_wall_shift = previous is not None and (
            (put_delta is not None and put_delta > 0)
            or (call_delta is not None and call_delta > 0)
        )
        if not bullish_wall_shift:
            continue

        spot = snapshot.get("spot")
        movement_score = sum(
            abs(_pct_of_spot(delta, spot) or 0)
            for delta in (call_delta, put_delta)
        )
        price_confirmed = shift_type in ("bullish", "bearish")
        ranking_score = movement_score + abs(spot_change_pct or 0) * 0.5
        radar.append({
            "ticker": ticker,
            "sector": snapshot.get("sector"),
            "spot": spot,
            "gamma_regime": snapshot.get("gamma_regime"),
            "shift_label": label,
            "shift_type": shift_type,
            "movement_score": round(movement_score, 2),
            "ranking_score": round(ranking_score, 2),
            "price_confirmed": price_confirmed,
            "previous_spot": previous_spot,
            "spot_change": spot_delta,
            "spot_change_pct": spot_change_pct,
            "previous_captured_at": previous and previous.get("captured_at"),
            "current_captured_at": captured_at,
            "previous_call_wall": previous and previous.get("call_wall"),
            "call_wall": snapshot.get("call_wall"),
            "call_wall_delta": call_delta,
            "previous_put_wall": previous and previous.get("put_wall"),
            "put_wall": snapshot.get("put_wall"),
            "put_wall_delta": put_delta,
            "previous_gamma_flip": previous and previous.get("gamma_flip"),
            "gamma_flip": snapshot.get("gamma_flip"),
            "gamma_flip_delta": flip_delta,
            "call_wall_upside_pct": _pct_of_spot(
                _number_delta(snapshot.get("call_wall"), spot), spot
            ),
            "put_wall_distance_pct": _pct_of_spot(
                _number_delta(spot, snapshot.get("put_wall")), spot
            ),
        })

    radar.sort(
        key=lambda row: (
            0 if row["shift_type"] == "bullish" else 1,
            -row["ranking_score"],
            row["ticker"],
        )
    )
    return radar[:WALL_SHIFT_TOP_N], history


def build_report():
    print(f"daily_update.py 직후 실행이라 {STARTUP_DELAY_SECONDS}초 대기 후 시작합니다...")
    time.sleep(STARTUP_DELAY_SECONDS)

    previous_report = _load_previous_report()
    categories = parse_watchlist(WATCHLIST_PATH)
    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at": generated_at,
        "categories": {},
    }

    for cat_key, tickers in categories.items():
        entries = []
        for item in tickers:
            print(f"  분석 중: {item['ticker']} ({cat_key})")
            entries.append(build_badge(item["ticker"], item["sector"]))
            time.sleep(PER_TICKER_DELAY_SECONDS)
        report["categories"][cat_key] = entries

    report["top10_gamma_flip"] = build_top10_gamma_flip()
    report["top_gainers"] = build_top_gainers()
    report["gamma_squeeze_candidates"] = build_gamma_squeeze_candidates(report["categories"])
    report["vanna_squeeze_candidates"] = build_vanna_squeeze_candidates(report["categories"])
    report["charm_squeeze_candidates"] = build_charm_squeeze_candidates(report["categories"])
    report["wall_shift_radar"], report["wall_history"] = build_wall_shift_radar(
        report["categories"], previous_report, generated_at
    )

    active_universe_tickers = load_or_build_active_universe()

    # ---- Call Wall 거래량 돌파 스캔 (수동 스윙 트레이딩용) ----
    report["call_wall_breakouts"] = build_call_wall_breakout_scan(
        active_universe_tickers, previous_report
    )

    # ---- Unusual Options Activity ("이상 옵션 거래") ----
    # 위와 동일한 active_universe_tickers를 재사용해서 추가 유니버스 재계산 없이 이어서 스캔한다.
    report["unusual_options_activity"] = build_unusual_options_activity(active_universe_tickers)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nleaders_report.json 생성 완료: {OUTPUT_PATH}")
    print(f"오늘의 급등주 Top: {len(report['top_gainers'])}개")
    print(f"감마 스퀴즈 후보: {len(report['gamma_squeeze_candidates'])}개")
    print(f"바나 스퀴즈 후보: {len(report['vanna_squeeze_candidates'])}개")
    print(f"차름 스퀴즈 후보: {len(report['charm_squeeze_candidates'])}개")
    print(f"Wall Shift Radar: {len(report['wall_shift_radar'])}개")
    print(f"Call Wall 거래량 돌파 후보: {len(report['call_wall_breakouts'])}개")
    print(f"이상 옵션 거래: {len(report['unusual_options_activity'])}개")


if __name__ == "__main__":
    build_report()
