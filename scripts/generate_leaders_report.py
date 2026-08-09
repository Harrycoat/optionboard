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
[추가] Top10 Gamma Flip 스캐너
S&P500 + 나스닥100 유니버스(sp500_nasdaq100_universe.txt)를 스캔해서,
현재가가 Gamma Flip 라인에 가장 가까이 붙어있는 종목 Top10을 뽑아
report["top10_gamma_flip"]에 저장한다. 감마 체제(양의감마<->음의감마) 전환이
임박했을 가능성이 있는 = 변동성 확대 후보로 해석한다.
analyze_ticker()는 만기 4개 옵션체인 + 420일 Stage 히스토리까지 조회하는
무거운 함수라, 520종목 스캔에는 quick_gamma_flip()(최근월물 1개만 조회)을
대신 사용해 API 호출을 최소화한다.
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
    MASSIVE_API_BASE,
    MASSIVE_API_KEY,
)

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "leaders_watchlist.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")
UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), "sp500_nasdaq100_universe.txt")

CATEGORY_KEYS = {"WAVE1": "wave1", "WAVE2": "wave2", "SPECULATIVE": "speculative"}

STARTUP_DELAY_SECONDS = 20
PER_TICKER_DELAY_SECONDS = 4
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = [8, 15]  # 재시도 1회차/2회차 대기시간

# Top10 Gamma Flip 스캐너 전용 설정.
# quick_gamma_flip()은 옵션체인 1개 만기만 조회하는 경량 호출이라
# 기존 4초 딜레이보다 훨씬 짧게 잡아도 된다 (Options Starter 플랜은 호출 무제한).
UNIVERSE_PER_TICKER_DELAY_SECONDS = 0.3
UNIVERSE_MAX_RETRIES = 1  # 전체 스캔이라 재시도는 최소화 (실패 종목은 그냥 스킵)
UNIVERSE_RETRY_BACKOFF_SECONDS = [5]


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
    """일봉 조회, 실패하면 재시도한다."""
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
    """analyze_ticker()를 시도하고, 실패하면 최대 MAX_RETRIES회 재시도한다."""
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
    """S&P500 + 나스닥100 유니버스 티커 리스트를 로드한다."""
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


def build_top10_gamma_flip(universe_path: str = UNIVERSE_PATH, top_n: int = 10) -> list:
    """유니버스 전체를 스캔해서 Gamma Flip 근접도(%) 기준 오름차순 Top N을 반환한다.

    즉 '현재가가 Gamma Flip 라인에 가장 가까이 붙어있는' 종목이 1위가 된다.
    이 구간은 감마 체제(양의 감마 <-> 음의 감마) 전환이 임박했을 가능성이 있어
    변동성 확대 후보로 해석한다.
    """
    tickers = load_universe(universe_path)
    print(f"\nGamma Flip 스캐너: {len(tickers)}개 종목 스캔 시작 (경량 모드)")

    candidates = []
    skipped = 0
    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0 or i == 1:
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