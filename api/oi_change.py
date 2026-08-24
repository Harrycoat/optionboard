"""
Vercel Python Serverless Function
GET /api/oi_change?ticker=TSLA

public/oi_history/{TICKER}.json에 저장된 스냅샷 중 가장 최근 2개(전일/오늘)를
비교해서 OI 롤오버(신규생성/청산/증가/감소)를 계산해 반환한다.

스냅샷은 scripts/snapshot_oi_history.py가 "오늘의 주도주" 구글시트 종목들에
대해 매일 밤 크론으로 저장한다. 이 파일이 없거나 스냅샷이 2개 미만이면
"데이터 축적 중" 상태를 반환한다.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import compute_oi_rollover  # noqa: E402

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "..", "public", "oi_history")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        ticker = (query.get("ticker", [""])[0]).strip().upper()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not ticker:
            self.wfile.write(json.dumps({"error": "ticker 파라미터가 필요합니다."}, ensure_ascii=False).encode())
            return

        path = os.path.join(HISTORY_DIR, f"{ticker}.json")

        if not os.path.exists(path):
            self.wfile.write(json.dumps({
                "ticker": ticker,
                "status": "no_history",
                "message": "이 종목은 아직 OI 히스토리 추적 대상이 아닙니다. '오늘의 주도주' 시트에 추가하시면 다음 날부터 데이터가 쌓여요.",
                "rollover": [],
            }, ensure_ascii=False).encode())
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.wfile.write(json.dumps({"error": f"히스토리 파일 읽기 실패: {e}", "ticker": ticker}, ensure_ascii=False).encode())
            return

        if len(history) < 2:
            self.wfile.write(json.dumps({
                "ticker": ticker,
                "status": "accumulating",
                "message": f"데이터 축적 중입니다 ({len(history)}일치 저장됨). 최소 2일치가 쌓여야 비교가 가능해요.",
                "rollover": [],
            }, ensure_ascii=False).encode())
            return

        prev_snapshot, today_snapshot = history[-2], history[-1]

        try:
            rollover = compute_oi_rollover(prev_snapshot, today_snapshot)
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
            return

        self.wfile.write(json.dumps({
            "ticker": ticker,
            "status": "ok",
            "prev_date": prev_snapshot.get("date"),
            "today_date": today_snapshot.get("date"),
            "prev_spot": prev_snapshot.get("spot"),
            "today_spot": today_snapshot.get("spot"),
            "rollover": rollover[:15],  # 변화량 큰 순으로 상위 15개만
        }, ensure_ascii=False).encode())