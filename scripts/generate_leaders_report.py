"""
scripts/generate_leaders_report.py

leaders_watchlist.txt를 읽어서 카테고리별(WAVE1/WAVE2/SPECULATIVE) 종목의
GEX 배지(Call Wall 근접도, Gamma Flip 레짐) + 현재가/등락률/거래량을 계산하고
public/leaders_report.json 으로 저장합니다.

- GEX 배지: options_engine.analyze_ticker() 재사용 (Massive.com API, 실측 gamma)
- 가격/등락률/거래량: Massive.com의 전일 일봉(prev bar) 엔드포인트 사용
  (options_engine._fetch_prev_close와 같은 엔드포인트, 여기서는 o/c/v를 전부 활용)

daily_update.py와 동일한 크론(.github/workflows/daily-update.yml)에서
이어서 호출하면 매일 자동 갱신됩니다.
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

# api/ 폴더의 options_engine을 import하기 위한 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from options_engine import analyze_ticker, MASSIVE_API_BASE, MASSIVE_API_KEY

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "leaders_watchlist.txt")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "leaders_report.json")

CATEGORY_KEYS = {"WAVE1": "wave1", "WAVE2": "wave2", "SPECULATIVE": "speculative"}


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
    """
    Massive.com의 전일 일봉(prev bar)을 가져온다.
    매일 장마감 후 크론이 도는 구조라, 이 'prev' 바 자체가 그날의 완결된 세션이라
    시가(o) 대비 종가(c)로 당일 등락률을 계산할 수 있다.
    """
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


def build_badge(ticker: str, sector: str) -> dict:
    """
    티커 하나에 대해 GEX 배지 + 가격/등락률/거래량을 계산하고
    프론트엔드 카드가 바로 쓸 수 있는 형태로 정리한다.
    개별 항목이 실패해도 사이트가 안 깨지도록 status 필드로 감싼다.
    """
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

    try:
        result = analyze_ticker(ticker)
        spot = result.get("spot")
        call_wall = result.get("call_wall")

        badge["spot"] = round(spot, 2) if spot is not None else None
        badge["call_wall"] = call_wall
        badge["put_wall"] = result.get("put_wall")
        badge["gamma_flip"] = result.get("gamma_flip")
        badge["gamma_regime"] = result.get("regime")

        if call_wall is not None and spot:
            badge["call_wall_distance_pct"] = round((call_wall - spot) / spot * 100, 2)

    except Exception as e:
        badge["status"] = f"error: {e}"

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
        report["categories"][cat_key] = entries

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nleaders_report.json 생성 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()