"""
scripts/generate_leaders_report.py

leaders_watchlist.txt를 읽어서 카테고리별(WAVE1/WAVE2/SPECULATIVE) 종목의
GEX 배지(Call Wall 근접도, Gamma Flip 레짐) + 현재가/등락률/거래량을 계산하고
public/leaders_report.json 으로 저장합니다.

- GEX 배지: options_engine.analyze_ticker() 재사용 (Massive.com API, 실측 gamma)
- 가격/등락률/거래량: Massive.com의 전일 일봉(prev bar) 엔드포인트 사용

daily_update.py 바로 다음에 같은 크론 잡 안에서 실행되는데, 두 스크립트가
연달아 Massive API를 많이 호출하다 보니 분당 호출 제한에 걸려 조용히
실패하는 경우가 있었다. 이를 완화하기 위해:
  1) 시작 전에 잠깐 대기해서 daily_update.py의 호출 버스트가 가라앉을 시간을 줌
  2) 종목 사이사이 딜레이를 둬서 호출을 분산시킴
  3) "현재가를 가져오지 못했습니다" 류의 일시적 실패는 한 번 재시도함

daily_update.py와 동일한 크론(.github/workflows/daily-update.yml)에서
이어서 호출하면 매일 자동 갱신됩니다.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# api/ 폴더의 options_engine을 import하기 위한 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from options_engine import analyze_ticker, MASSIVE_API_BASE, MASSIVE_API_KEY

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "leaders_watchlist.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")

CATEGORY_KEYS = {"WAVE1": "wave1", "WAVE2": "wave2", "SPECULATIVE": "speculative"}

# daily_update.py 직후 실행되므로, API 호출 버스트가 가라앉을 시간을 준다
STARTUP_DELAY_SECONDS = 15
# 종목과 종목 사이 대기 시간 (분당 호출 제한 완화용)
PER_TICKER_DELAY_SECONDS = 3
# 실패 시 재시도 전 대기 시간
RETRY_DELAY_SECONDS = 8


def parse_watchlist(path):
    """[WAVE1] 등 헤더로 구분된 watchlist 파일을 카테고리별 dict로 파싱"""
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


def fetch_daily_bar(ticker: str):
    """Massive.com의 전일 일봉(prev bar)을 가져온다."""
    if not MASSIVE_API_KEY:
        return None
    url = f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/prev"
    try:
        resp = requests.get(url, params={"apiKey": MASSIVE_API_KEY}, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        return {"open": r.get("o"), "close": r.get("c"), "volume": r.get("v")}
    except Exception:
        return None


def try_analyze(ticker: str):
    """analyze_ticker()를 시도하고, 실패하면 한 번 더 재시도한다."""
    try:
        return analyze_ticker(ticker), None
    except Exception as e:
        print(f"    1차 실패 ({ticker}): {e} → {RETRY_DELAY_SECONDS}초 후 재시도")
        time.sleep(RETRY_DELAY_SECONDS)
        try:
            return analyze_ticker(ticker), None
        except Exception as e2:
            return None, str(e2)


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

    # 가격/등락률/거래량은 GEX 계산이 실패해도 별도로 시도한다
    bar = fetch_daily_bar(ticker)
    if bar:
        badge["volume"] = bar.get("volume")
        o, c = bar.get("open"), bar.get("close")
        if o and c:
            badge["price_change_pct"] = round((c - o) / o * 100, 2)
        if badge["spot"] is None and c:
            badge["spot"] = round(c, 2)

    return badge


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

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nleaders_report.json 생성 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()