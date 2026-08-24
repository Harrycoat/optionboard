"""
scripts/snapshot_oi_history.py

"오늘의 주도주" 구글시트에 있는 종목들의 스트라이크별 OI(미결제약정)+거래량
스냅샷을 매일 저장한다. 저장된 스냅샷은 public/oi_history/{TICKER}.json에
날짜별로 누적되며, /api/oi_change 엔드포인트가 이 파일의 "가장 최근 2개
항목"을 비교해서 OI 롤오버(신규생성/청산)를 계산하는 데 쓰인다.

★ 종목 리스트는 별도로 관리하지 않는다 ★
index.html의 "오늘의 주도주" 보드가 읽는 구글시트(TICKER_SHEET_CSV_URL)를
그대로 재사용한다 — 시트에 종목을 추가/삭제하면 다음 크론부터 자동으로
OI 추적 대상도 같이 바뀐다 (코드 수정 불필요).

daily_update.py, generate_leaders_report.py와 같은 크론(.github/workflows/
daily-update.yml)에서 이어서 실행된다.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import fetch_oi_volume_snapshot  # noqa: E402

# index.html의 TICKER_SHEET_CSV_URL과 반드시 동일해야 함
TICKER_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSXwE0ik6itZFGTe6irbrniOtzQ1a3dXR-fg8hYdaiUotQRpzmseG5BdoHpU-w330-GzhttZXrDldVJ/pub?output=csv"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "public", "oi_history")
MAX_HISTORY_DAYS = 14  # 파일 크기 관리를 위해 최근 N일치만 보관
PER_TICKER_DELAY_SECONDS = 4
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = [8, 15]


def fetch_tickers_from_sheet(csv_url: str) -> list[str]:
    """index.html의 fetchTickersFromSheet()와 동일한 파싱 규칙 (A열=티커)."""
    resp = requests.get(csv_url, timeout=15)
    resp.raise_for_status()
    tickers = []
    for line in resp.text.splitlines():
        parts = line.split(",")
        t = (parts[0] or "").strip().upper()
        if not t or not t.isalpha() or len(t) > 6 or t == "TICKER":
            continue
        tickers.append(t)
    return tickers[:10]  # 프론트엔드와 동일하게 상위 10개만


def try_snapshot(ticker: str):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fetch_oi_volume_snapshot(ticker), None
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[attempt]
                print(f"    {ticker} 스냅샷 실패 ({attempt+1}차): {e} → {wait}초 후 재시도")
                time.sleep(wait)
            else:
                return None, str(e)


def append_snapshot(ticker: str, snapshot: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{ticker}.json")

    history = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = []

    # 같은 날짜에 이미 기록이 있으면 덮어쓰기 (하루 여러 번 실행돼도 중복 안 쌓이게)
    today = snapshot["date"]
    history = [h for h in history if h.get("date") != today]
    history.append(snapshot)
    history = history[-MAX_HISTORY_DAYS:]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def main():
    try:
        tickers = fetch_tickers_from_sheet(TICKER_SHEET_CSV_URL)
    except Exception as e:
        print(f"구글시트에서 종목 리스트를 가져오지 못했습니다: {e}")
        return

    print(f"OI 히스토리 스냅샷 저장 시작: {len(tickers)}개 종목 ({', '.join(tickers)})")

    saved, failed = 0, []
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {ticker} 스냅샷 조회 중...")
        snapshot, err = try_snapshot(ticker)
        if snapshot:
            append_snapshot(ticker, snapshot)
            saved += 1
        else:
            failed.append((ticker, err))
        time.sleep(PER_TICKER_DELAY_SECONDS)

    print(f"\n완료: {saved}개 저장 성공, {len(failed)}개 실패")
    for t, err in failed:
        print(f"  실패: {t} — {err}")


if __name__ == "__main__":
    main()