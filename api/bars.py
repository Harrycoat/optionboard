"""
Vercel Python Serverless Function
GET /api/bars?ticker=AAPL

캔들차트(lightweight-charts)용 일봉 OHLC 데이터와,
차트 위에 겹쳐 그릴 GEX 레벨(Call Wall/Put Wall/Gamma Flip/Max Pain/Max OI Wall)을 함께 반환한다.

NOTE: /api/candles 경로가 Vercel에서 search.py 핸들러로 잘못 라우팅되는 문제가 있어
이 파일(/api/bars)을 새 경로로 사용한다. candles.py는 남겨두되 프론트엔드는 이쪽을 호출한다.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import analyze_ticker_cached, fetch_daily_ohlc  # noqa: E402


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
            self.wfile.write(json.dumps({"error": "ticker 파라미터가 필요합니다. 예: /api/bars?ticker=AAPL"}, ensure_ascii=False).encode())
            return

        try:
            bars, ohlc_debug = fetch_daily_ohlc(ticker)

            levels = {}
            try:
                # skip_stage=True: 이 오버레이는 Stage(추세단계) 값을 쓰지 않으므로,
                # 420일치 일봉을 별도로 더 조회하는 Stage 계산 단계를 건너뛴다.
                gex = analyze_ticker_cached(ticker, skip_stage=True)
                levels = {
                    "spot": gex.get("spot"),
                    "max_pain": gex.get("max_pain"),
                    "call_wall": gex.get("call_wall"),
                    "put_wall": gex.get("put_wall"),
                    "gamma_flip": gex.get("gamma_flip"),
                    "max_oi_wall": gex.get("max_oi_wall"),
                }
            except Exception:
                pass

            self.wfile.write(json.dumps({
                "ticker": ticker,
                "bars": bars,
                "levels": levels,
                "ohlc_debug": ohlc_debug,
            }, ensure_ascii=False).encode())
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
