"""
매일 GitHub Actions에서 실행되는 관심종목 자동 리포트 생성 스크립트.
결과를 public/watchlist_report.json 에 저장 -> git commit -> Vercel 자동 재배포.

관심종목 리스트는 watchlist.txt 에서 한 줄에 하나씩 관리.

---
[429 대응]
Massive API 요금제 페이지에는 "Unlimited API Calls"라고 되어 있지만, 실제로는
분당 요청 횟수 제한이 있다 (에러 메시지: "You've exceeded the maximum
requests per minute"). analyze_ticker() 하나가 내부적으로 여러 번 API를
호출하기 때문에(옵션체인 페이지네이션 + 일봉 range + 현재가 폴백), 종목 사이
딜레이를 늘리고 실패 시 재시도하도록 한다. 이 값들은 generate_leaders_report.py
에서 실전 검증된 것과 동일하다.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import analyze_ticker  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
WATCHLIST_FILE = os.path.join(ROOT, "scripts", "watchlist.txt")
OUTPUT_FILE = os.path.join(ROOT, "public", "watchlist_report.json")

PER_TICKER_DELAY_SECONDS = 4
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = [8, 15]  # 재시도 1회차/2회차 대기시간


def load_watchlist() -> list[str]:
    if not os.path.exists(WATCHLIST_FILE):
        return ["SPY", "QQQ", "NET", "MU", "SNDK", "TSLA"]
    with open(WATCHLIST_FILE, "r") as f:
        return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]


def try_analyze(ticker: str):
    """analyze_ticker()를 시도하고, 실패하면 최대 MAX_RETRIES회 재시도한다."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return analyze_ticker(ticker), None
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[attempt]
                print(f"    {ticker} 실패 ({attempt+1}차): {e} → {wait}초 후 재시도")
                time.sleep(wait)
            else:
                return None, str(e)


def main():
    tickers = load_watchlist()
    results = []
    errors = []

    for t in tickers:
        result, err = try_analyze(t)
        if result:
            # 리포트 용량을 줄이기 위해 pain_curve/gex_by_strike 상세는 제외하고 요약만 저장
            summary = {k: v for k, v in result.items() if k not in ("pain_curve", "gex_by_strike")}
            results.append(summary)
            print(f"OK  {t}: max_pain={result['max_pain']} call_wall={result['call_wall']} put_wall={result['put_wall']}")
        else:
            errors.append({"ticker": t, "error": err})
            print(f"FAIL {t}: {err}")
        time.sleep(PER_TICKER_DELAY_SECONDS)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": results,
        "errors": errors,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료: {OUTPUT_FILE} ({len(results)}개 성공, {len(errors)}개 실패)")


if __name__ == "__main__":
    main()