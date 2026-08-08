"""
매일 GitHub Actions에서 실행되는 관심종목 자동 리포트 생성 스크립트.
결과를 public/watchlist_report.json 에 저장 -> git commit -> Vercel 자동 재배포.

관심종목 리스트는 watchlist.txt 에서 한 줄에 하나씩 관리.
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


def load_watchlist() -> list[str]:
    if not os.path.exists(WATCHLIST_FILE):
        return ["SPY", "QQQ", "NET", "MU", "SNDK", "TSLA"]
    with open(WATCHLIST_FILE, "r") as f:
        return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]


def main():
    tickers = load_watchlist()
    results = []
    errors = []

    for t in tickers:
        try:
            r = analyze_ticker(t)
            # 리포트 용량을 줄이기 위해 pain_curve/gex_by_strike 상세는 제외하고 요약만 저장
            summary = {k: v for k, v in r.items() if k not in ("pain_curve", "gex_by_strike")}
            results.append(summary)
            print(f"OK  {t}: max_pain={r['max_pain']} call_wall={r['call_wall']} put_wall={r['put_wall']}")
        except Exception as e:
            errors.append({"ticker": t, "error": str(e)})
            print(f"FAIL {t}: {e}")
        time.sleep(1.5)  # Yahoo rate limit 완화용 딜레이

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
