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

---
[Dev% 재진입 스캐너 — "오늘의 매수 신호"]

active_universe.txt(100종목)를 그대로 재사용해서, Hull21 이동평균 + Dev%
괴리율 기반 재진입 신호(TOS ThinkScript Hull_Deviation_Reentry_v3와 동일 로직)를
계산한다. dev_reentry_scanner.py에 로직이 분리되어 있고, 여기서는 결과만
받아서 리포트에 합친다.

---
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
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import (
    analyze_ticker,
    quick_gamma_flip,
    rank_by_liquidity,
    fetch_oi_volume_snapshot,
    MASSIVE_API_BASE,
    MASSIVE_API_KEY,
)
from dev_reentry_scanner import build_dev_reentry_signals  # noqa: E402

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
    report["top_gainers"] = build_top_gainers()
    report["gamma_squeeze_candidates"] = build_gamma_squeeze_candidates(report["categories"])
    report["vanna_squeeze_candidates"] = build_vanna_squeeze_candidates(report["categories"])
    report["charm_squeeze_candidates"] = build_charm_squeeze_candidates(report["categories"])

    # ---- Dev% 재진입 스캐너 ("오늘의 매수 신호") ----
    # active_universe(100종목)를 재사용해서 추가 API 호출 부담 없이 이어서 스캔한다.
    active_universe_tickers = load_or_build_active_universe()
    dev_signals = build_dev_reentry_signals(active_universe_tickers)
    report["dev_reentry_long"] = dev_signals["long_reentry"]
    report["dev_reentry_short_exit"] = dev_signals["short_exit"]

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
    print(f"Dev 롱 재진입: {len(report['dev_reentry_long'])}개")
    print(f"Dev 숏·청산: {len(report['dev_reentry_short_exit'])}개")
    print(f"이상 옵션 거래: {len(report['unusual_options_activity'])}개")


if __name__ == "__main__":
    build_report()
