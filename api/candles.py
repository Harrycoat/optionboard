"""
Vercel Python Serverless Function
GET /api/candles?ticker=AAPL

캔들차트(lightweight-charts)용 일봉 OHLC 데이터와,
차트 위에 겹쳐 그릴 GEX 레벨(Call Wall/Put Wall/Gamma Flip/Max Pain)을 함께 반환한다.
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
            self.wfile.write(json.dumps({"error": "ticker 파라미터가 필요합니다. 예: /api/candles?ticker=AAPL"}, ensure_ascii=False).encode())
            return

        try:
            bars, ohlc_debug = fetch_daily_ohlc(ticker)

            levels = {}
            try:
                # skip_stage=True: 이 오버레이는 Stage(추세단계) 값을 쓰지 않으므로,
                # 420일치 일봉을 별도로 더 조회하는 Stage 계산 단계를 건너뛴다.
                # (analyze_ticker()가 원래 하던 네트워크 왕복을 하나 줄여서
                #  캔들차트 응답이 느려지거나 타임아웃 나는 걸 방지)
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
