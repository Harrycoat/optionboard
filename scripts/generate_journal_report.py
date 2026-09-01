"""
scripts/generate_journal_report.py

해리님이 구글시트에 직접 입력하는 트레이딩 저널(휠 전략 CSP/CC 매매 원장)을
읽어서, public/journal_report.json 을 생성한다.

- "청산" 상태인 행들 -> 승률 / 평균 손익 / 손익비(payoff ratio) 집계
- "진행중"/"배정" 상태인 행들 -> 현재가를 다시 조회해서 스트라이크 대비
  거리(%), 만기까지 D-day 를 계산해서 "지금 이 포지션이 어떤 상태인지"를
  최신으로 유지 (구글시트 자체는 해리님이 이벤트 있을 때만 손으로 업데이트하고,
  이 스크립트가 장중에 주기적으로 돌면서 "현재 상태"만 자동으로 계산해준다)

구글시트 준비 방법:
  1. 구글시트 파일을 열고 공유 설정을 "링크가 있는 모든 사용자 - 뷰어"로 변경
  2. 주소창의 스프레드시트 ID를 확인 (docs.google.com/spreadsheets/d/여기부분/edit)
  3. 아래 형태의 CSV export 주소를 만든다 (gid는 특정 탭의 ID, 기본 탭이면 보통 0):
     https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}
  4. 이 주소를 GitHub 저장소 Settings > Secrets and variables > Actions 에
     JOURNAL_SHEET_CSV_URL 이름으로 등록한다.

헤더(컬럼) 순서는 아래를 그대로 기준으로 한다 (앞뒤 공백은 자동으로 무시됨):
  날짜 | 티커 | 전략(CSP/CC/기타) | 스트라이크 | 진입가(프리미엄) | 계약수 |
  만기일 | 상태(진행중/배정/청산) | 청산가 | 손익 |
  진입시점_PutWall | 진입시점_CallWall | 진입시점_GammaFlip | 진입시점_MaxPain | 메모
"""
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from options_engine import quick_gamma_flip  # noqa: E402

SHEET_CSV_URL = os.environ.get("JOURNAL_SHEET_CSV_URL", "").strip()
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "journal_report.json")

CLOSED_STATUSES = {"청산", "종료", "closed"}


def _clean(s):
    return (s or "").strip()


def _to_float(s):
    s = _clean(s)
    if not s:
        return None
    try:
        return float(s.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _parse_date(s):
    """여러 흔한 표기(08-31-2026, 2026-08-31, 2026-08-31T00:00:00 등)를 다 받아본다."""
    s = _clean(s)
    if not s:
        return None
    fmts = ["%m-%d-%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"]
    for fmt in fmts:
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    return None


def fetch_rows():
    if not SHEET_CSV_URL:
        raise RuntimeError("JOURNAL_SHEET_CSV_URL 환경변수가 설정되지 않았습니다.")
    resp = requests.get(SHEET_CSV_URL, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    # 헤더 앞뒤 공백 제거 (해리님 시트에 '스트라이크 ', ' 손익' 처럼 공백 섞여 있어도 안전하게)
    reader.fieldnames = [(_clean(h)) for h in reader.fieldnames]
    rows = []
    for raw in reader:
        row = {_clean(k): _clean(v) for k, v in raw.items()}
        if not row.get("티커"):
            continue
        rows.append(row)
    return rows


def build_report(rows):
    closed = []
    open_trades = []

    for row in rows:
        status = row.get("상태(진행중/배정/청산)", "")
        pnl = _to_float(row.get("손익"))

        if status in CLOSED_STATUSES:
            closed.append({
                "date": row.get("날짜"),
                "ticker": row.get("티커"),
                "strategy": row.get("전략(CSP/CC/기타)"),
                "strike": _to_float(row.get("스트라이크")),
                "entry_premium": _to_float(row.get("진입가(프리미엄)")),
                "contracts": _to_float(row.get("계약수")),
                "expiry": row.get("만기일"),
                "exit_price": _to_float(row.get("청산가")),
                "pnl": pnl,
                "entry_put_wall": _to_float(row.get("진입시점_PutWall")),
                "entry_call_wall": _to_float(row.get("진입시점_CallWall")),
                "entry_gamma_flip": _to_float(row.get("진입시점_GammaFlip")),
                "entry_max_pain": _to_float(row.get("진입시점_MaxPain")),
                "note": row.get("메모"),
            })
        else:
            open_trades.append(row)

    # ---- 승률 / 손익비 집계 (청산된 트레이드만 대상) ----
    pnl_values = [t["pnl"] for t in closed if t["pnl"] is not None]
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    win_rate = round(len(wins) / len(pnl_values) * 100, 1) if pnl_values else None
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    payoff_ratio = round(avg_win / abs(avg_loss), 2) if avg_win and avg_loss else None
    total_realized_pnl = round(sum(pnl_values), 2) if pnl_values else None

    summary = {
        "total_closed_trades": len(closed),
        "total_with_pnl": len(pnl_values),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "total_realized_pnl": total_realized_pnl,
    }

    # ---- 진행중/배정 포지션 -> 현재가 다시 조회해서 실시간 상태 계산 ----
    live_cache = {}
    open_result = []
    today = date.today()

    for row in open_trades:
        ticker = row.get("티커", "").upper()
        strike = _to_float(row.get("스트라이크"))
        expiry_d = _parse_date(row.get("만기일"))

        live = live_cache.get(ticker)
        if live is None:
            try:
                live = quick_gamma_flip(ticker)
            except Exception as e:
                live = {"error": str(e)}
            live_cache[ticker] = live

        spot = live.get("spot") if isinstance(live, dict) else None
        dist_pct = None
        if spot and strike:
            dist_pct = round((spot - strike) / strike * 100, 2)

        dte = (expiry_d - today).days if expiry_d else None

        open_result.append({
            "date": row.get("날짜"),
            "ticker": ticker,
            "strategy": row.get("전략(CSP/CC/기타)"),
            "strike": strike,
            "entry_premium": _to_float(row.get("진입가(프리미엄)")),
            "contracts": _to_float(row.get("계약수")),
            "expiry": row.get("만기일"),
            "status": row.get("상태(진행중/배정/청산)"),
            "entry_put_wall": _to_float(row.get("진입시점_PutWall")),
            "entry_call_wall": _to_float(row.get("진입시점_CallWall")),
            "entry_gamma_flip": _to_float(row.get("진입시점_GammaFlip")),
            "entry_max_pain": _to_float(row.get("진입시점_MaxPain")),
            "note": row.get("메모"),
            "current_spot": spot,
            "current_gamma_flip": live.get("gamma_flip") if isinstance(live, dict) else None,
            "current_regime": live.get("regime") if isinstance(live, dict) else None,
            "distance_to_strike_pct": dist_pct,
            "days_to_expiry": dte,
            "live_fetch_error": live.get("error") if isinstance(live, dict) else None,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "closed_trades": closed,
        "open_trades": open_result,
    }


def main():
    rows = fetch_rows()
    report = build_report(rows)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"journal_report.json 저장 완료: 청산 {len(report['closed_trades'])}건, 진행중 {len(report['open_trades'])}건")


if __name__ == "__main__":
    main()
