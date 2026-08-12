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
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from options_engine import (
    analyze_ticker,
    quick_gamma_flip,
    rank_by_liquidity,
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
def load_universe(path: str) -> list:
    """티커 리스트 파일을 로드한다 (주석/빈 줄 제외, 한 줄에 티커 하나)."""
    tickers = []
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line)
    return tickers


def compute_flip_distance_pct(spot, gamma_flip):
    """현재가가 Gamma Flip 라인에서 얼마나 떨어져 있는지 절대값 %로 계산한다."""
    if spot is None or gamma_flip is None or spot == 0:
        return None
    return abs(spot - gamma_flip) / spot * 100


def try_quick_flip(ticker: str):
    """quick_gamma_flip()을 시도하고, 실패하면 최대 UNIVERSE_MAX_RETRIES회 재시도한다."""
    for attempt in range(UNIVERSE_MAX_RETRIES + 1):
        try:
            return quick_gamma_flip(ticker), None
        except Exception as e:
            if attempt < UNIVERSE_MAX_RETRIES:
                wait = UNIVERSE_RETRY_BACKOFF_SECONDS[attempt]
                time.sleep(wait)
            else:
                return None, str(e)


def try_rank_liquidity(ticker: str):
    """rank_by_liquidity()를 시도하고, 실패하면 최대 LIQUIDITY_MAX_RETRIES회 재시도한다."""
    for attempt in range(LIQUIDITY_MAX_RETRIES + 1):
        try:
            return rank_by_liquidity(ticker), None
        except Exception as e:
            if attempt < LIQUIDITY_MAX_RETRIES:
                wait = LIQUIDITY_RETRY_BACKOFF_SECONDS[attempt]
                time.sleep(wait)
            else:
                return None, str(e)


def build_active_universe(
    universe_path: str = UNIVERSE_PATH,
    output_path: str = ACTIVE_UNIVERSE_PATH,
    top_n: int = ACTIVE_UNIVERSE_TOP_N,
) -> list:
    """전체 유니버스를 OI(유동성) 기준으로 스캔해서 상위 top_n개를 뽑아 저장한다.

    현재가 폴백 호출이 없어 종목당 API 호출이 1번뿐이라 429 문제가 크게
    줄어든다. 이 함수는 주 1회(월요일)만 실행된다.
    """
    tickers = load_universe(universe_path)
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
    """오늘이 월요일이거나 active_universe.txt가 없으면 새로 스캔하고,
    그 외에는 기존 파일을 그대로 읽는다."""
    is_scan_day = datetime.now(timezone.utc).weekday() == LIQUIDITY_SCAN_WEEKDAY
    file_exists = os.path.exists(ACTIVE_UNIVERSE_PATH)

    if is_scan_day or not file_exists:
        reason = "월요일" if is_scan_day else "active_universe.txt 없음"
        print(f"\n유동성 재스캔 조건 충족 ({reason}) — 전체 유니버스 스캔 실행")
        return build_active_universe()
    else:
        print(f"\n기존 active_universe.txt 재사용 (다음 갱신: 월요일)")
        return load_universe(ACTIVE_UNIVERSE_PATH)


def build_top10_gamma_flip(top_n: int = 10) -> list:
    """유동성 상위 종목(active_universe)을 스캔해서 Gamma Flip 근접도(%) 기준
    오름차순 Top N을 반환한다."""
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


def build_report():
    print(f"daily_update.py 직후 실행이라 {STARTUP_DELAY_SECONDS}초 대기 후 시작합니다...")
    time.sleep(STARTUP_DELAY_SECONDS)

    categories = parse_watchlist(WATCHLIST_PATH)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nleaders_report.json 생성 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
