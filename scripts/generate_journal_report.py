"""
scripts/generate_journal_report.py

해리님이 구글시트에 직접 입력하는 트레이딩 저널(Put Wall 매수 주식 스윙 원장)을
읽어서, public/journal_report.json 을 생성한다.

v2: CSP/CC 옵션 휠 전략 기록에서 "Put Wall 매수 → 주식 스윙" 기록으로 전면
교체했다 (일반 독자에게는 CC/CSP보다 "얼마에 사서 얼마에 팔았다"가 훨씬
이해하기 쉽다는 판단).

- "청산" 상태인 행들 -> 승률 / 평균 손익률 / 손익비(payoff ratio) / 누적 실현손익($) 집계
- "진행중" 상태인 행들 -> 현재가를 다시 조회해서 매수가 대비 미실현 수익률(%)과
  금액($)을 계산하고, put_wall_ftd_signal.py와 동일한 로직(Hull21/50 데드크로스,
  150일선 이탈)으로 "구조 유지" / "청산 신호 발생" 상태까지 함께 보여준다
  (구글시트 자체는 해리님이 이벤트 있을 때만 손으로 업데이트하고, 이 스크립트가
  장중에 주기적으로 돌면서 "현재 상태"만 자동으로 계산해준다)

구글시트 준비 방법:
  1. 구글시트 파일을 열고 공유 설정을 "링크가 있는 모든 사용자 - 뷰어"로 변경
  2. 주소창의 스프레드시트 ID를 확인 (docs.google.com/spreadsheets/d/여기부분/edit)
  3. 아래 형태의 CSV export 주소를 만든다 (gid는 특정 탭의 ID, 기본 탭이면 보통 0):
     https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}
  4. 이 주소를 GitHub 저장소 Settings > Secrets and variables > Actions 에
     JOURNAL_SHEET_CSV_URL 이름으로 등록한다.

헤더(컬럼) 순서는 아래를 그대로 기준으로 한다 (앞뒤 공백은 자동으로 무시됨):
  매수일자 | 티커 | PutWall | PutWall_일자 | 매수가 | 수량 |
  상태(진행중/청산) | 청산일자 | 청산가 | 메모

  예시 행: 09-02-2026 | PLTR | 165 | 09-01-2026 | 167.00 | 100 | 진행중 | | | 풋월 반등 확인 후 진입
"""
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.dirname(__file__))

from options_engine import quick_gamma_flip, fetch_daily_ohlc  # noqa: E402
from dev_reentry_scanner import hull_ma_series  # noqa: E402
from put_wall_ftd_signal import (  # noqa: E402
    HULL_FAST_PERIOD,
    HULL_SLOW_PERIOD,
    SMA_PERIOD,
    LOOKBACK_DAYS_REQUEST,
    sma_series,
    structural_exit_signals,
)

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


def _fmt_or_dash(v):
    return v if v is not None and v != "" else "—"


def _pct(buy_price, price):
    if not buy_price or price is None:
        return None
    return round((price - buy_price) / buy_price * 100, 2)


def _dollar_pnl(buy_price, price, quantity):
    if buy_price is None or price is None or not quantity:
        return None
    return round((price - buy_price) * quantity, 2)


# ---------------------------------------------------------------------------
# 구조적 상태 (Hull21/50 데드크로스, 150일선 이탈) — put_wall_ftd_signal.py와
# 동일한 계산을 그대로 재사용한다. 종목별로 한 번만 일봉을 조회하도록 캐싱한다.
# ---------------------------------------------------------------------------
def compute_structural_status(ticker, bars_cache):
    if ticker in bars_cache:
        bars = bars_cache[ticker]
    else:
        try:
            bars, _debug = fetch_daily_ohlc(ticker, lookback_days=LOOKBACK_DAYS_REQUEST)
        except Exception:
            bars = None
        bars_cache[ticker] = bars

    if not bars or len(bars) < SMA_PERIOD + 5:
        return {"status": None, "reasons": [], "error": "일봉 데이터 부족"}

    closes = [b["close"] for b in bars]
    dates = [b["time"] for b in bars]
    hull_fast = hull_ma_series(closes, period=HULL_FAST_PERIOD)
    hull_slow = hull_ma_series(closes, period=HULL_SLOW_PERIOD)
    sma150 = sma_series(closes, period=SMA_PERIOD)

    reasons = structural_exit_signals(closes, dates, hull_fast, hull_slow, sma150)
    status = "청산신호" if reasons else "구조유지"
    return {"status": status, "reasons": reasons, "error": None}


def build_case_study_draft(trade):
    """청산된 트레이드 1건을 블로그용 케이스 스터디 초안(제목+본문)으로 변환한다.
    해리님이 이 텍스트를 그대로/조금 다듬어서 블로그에 반자동으로 올릴 수 있게
    (자동 발행은 아니고, 복사해서 붙여넣는 용도의 '초안'만 생성한다)."""
    ticker = trade.get("ticker") or "—"
    buy_date = trade.get("buy_date") or "—"
    put_wall = trade.get("entry_put_wall")
    put_wall_date = trade.get("entry_put_wall_date")
    buy_price = trade.get("buy_price")
    quantity = trade.get("quantity")
    exit_date = trade.get("exit_date") or "—"
    exit_price = trade.get("exit_price")
    final_return_pct = trade.get("final_return_pct")
    final_pnl = trade.get("final_pnl")
    note = trade.get("note") or ""

    if final_return_pct is None:
        result_word = "결과 미기록"
    elif final_return_pct > 0:
        result_word = "승 (수익)"
    elif final_return_pct < 0:
        result_word = "패 (손실)"
    else:
        result_word = "본전"
    return_text = f"{'+' if (final_return_pct is not None and final_return_pct > 0) else ''}{final_return_pct}%" if final_return_pct is not None else "—"
    pnl_text = f"{'+' if (final_pnl is not None and final_pnl > 0) else ''}${final_pnl}" if final_pnl is not None else "—"

    title = f"[스윙 저널] {ticker} Put Wall 매수 케이스 스터디 ({buy_date} 진입)"

    lines = [
        f"이번 트레이드는 {buy_date}에 {ticker} 종목을 Put Wall 근처에서 매수한 건입니다.",
        "",
        "■ 진입 정보",
        f"- 매수일자: {buy_date}",
        f"- 매수가: {_fmt_or_dash(buy_price)}",
        f"- 수량: {_fmt_or_dash(quantity)}",
        f"- 진입 근거 Put Wall: {_fmt_or_dash(put_wall)} ({put_wall_date or '—'} 기준)",
    ]
    if note:
        lines.append(f"- 메모: {note}")
    lines += [
        "",
        "■ 결과",
        f"- 청산일자: {exit_date}",
        f"- 청산가: {_fmt_or_dash(exit_price)}",
        f"- 수익률: {return_text}",
        f"- 손익: {pnl_text}",
        f"- 결과: {result_word}",
        "",
        "■ 총평",
        "(이번 트레이드에서 느낀 점, 다음에 다르게 할 부분을 여기에 자유롭게 적어주세요.)",
    ]
    return {"title": title, "body": "\n".join(lines)}


def fetch_rows():
    if not SHEET_CSV_URL:
        raise RuntimeError("JOURNAL_SHEET_CSV_URL 환경변수가 설정되지 않았습니다.")
    resp = requests.get(SHEET_CSV_URL, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    # 헤더 앞뒤 공백 제거 (시트에 '매수가 ', ' 수량'처럼 공백 섞여 있어도 안전하게)
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
        status = row.get("상태(진행중/청산)", "")
        buy_price = _to_float(row.get("매수가"))
        quantity = _to_float(row.get("수량"))

        if status in CLOSED_STATUSES:
            exit_price = _to_float(row.get("청산가"))
            closed_trade = {
                "buy_date": row.get("매수일자"),
                "ticker": row.get("티커", "").upper(),
                "entry_put_wall": _to_float(row.get("PutWall")),
                "entry_put_wall_date": row.get("PutWall_일자"),
                "buy_price": buy_price,
                "quantity": quantity,
                "exit_date": row.get("청산일자"),
                "exit_price": exit_price,
                "final_return_pct": _pct(buy_price, exit_price),
                "final_pnl": _dollar_pnl(buy_price, exit_price, quantity),
                "note": row.get("메모"),
            }
            # 블로그에 반자동으로 올릴 케이스 스터디 초안(제목+본문)을 미리 만들어 둔다.
            closed_trade["draft"] = build_case_study_draft(closed_trade)
            closed.append(closed_trade)
        else:
            open_trades.append(row)

    # ---- 승률 / 손익비 / 누적 실현손익 집계 (청산된 트레이드만 대상) ----
    return_values = [t["final_return_pct"] for t in closed if t["final_return_pct"] is not None]
    pnl_values = [t["final_pnl"] for t in closed if t["final_pnl"] is not None]
    wins = [p for p in return_values if p > 0]
    losses = [p for p in return_values if p < 0]
    win_rate = round(len(wins) / len(return_values) * 100, 1) if return_values else None
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    payoff_ratio = round(avg_win / abs(avg_loss), 2) if avg_win and avg_loss else None
    total_realized_pnl = round(sum(pnl_values), 2) if pnl_values else None

    summary = {
        "total_closed_trades": len(closed),
        "total_with_return": len(return_values),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": payoff_ratio,
        "total_realized_pnl": total_realized_pnl,
    }

    # ---- 진행중 포지션 -> 현재가/구조 상태 다시 조회해서 실시간 계산 ----
    live_cache = {}
    bars_cache = {}
    open_result = []

    for row in open_trades:
        ticker = row.get("티커", "").upper()
        row_buy_price = _to_float(row.get("매수가"))
        row_quantity = _to_float(row.get("수량"))

        live = live_cache.get(ticker)
        if live is None:
            try:
                live = quick_gamma_flip(ticker)
            except Exception as e:
                live = {"error": str(e)}
            live_cache[ticker] = live

        spot = live.get("spot") if isinstance(live, dict) else None
        structural = compute_structural_status(ticker, bars_cache)

        open_result.append({
            "buy_date": row.get("매수일자"),
            "ticker": ticker,
            "entry_put_wall": _to_float(row.get("PutWall")),
            "entry_put_wall_date": row.get("PutWall_일자"),
            "buy_price": row_buy_price,
            "quantity": row_quantity,
            "status": row.get("상태(진행중/청산)"),
            "note": row.get("메모"),
            "current_spot": spot,
            "current_gamma_flip": live.get("gamma_flip") if isinstance(live, dict) else None,
            "current_regime": live.get("regime") if isinstance(live, dict) else None,
            "return_pct": _pct(row_buy_price, spot),
            "unrealized_pnl": _dollar_pnl(row_buy_price, spot, row_quantity),
            "structural_status": structural["status"],
            "structural_reasons": structural["reasons"],
            "live_fetch_error": live.get("error") if isinstance(live, dict) else structural.get("error"),
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
