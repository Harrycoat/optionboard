"""
Vercel Python Serverless Function
GET /api/search?ticker=AAPL
GET /api/search?mode=earnings_scan&watchlist=AAPL,NVDA,ANET
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import analyze_ticker_cached  # noqa: E402
from earnings_engine import (  # noqa: E402
    scan_earnings_movers,
    scan_earnings_movers_from_watchlist,
    _mover_to_dict,
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        mode = (query.get("mode", [""])[0]).strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if mode == "earnings_scan":
            self._handle_earnings_scan(query)
            return

        ticker = (query.get("ticker", [""])[0]).strip()
        if not ticker:
            self.wfile.write(json.dumps({"error": "ticker 파라미터가 필요합니다. 예: /api/search?ticker=AAPL"}, ensure_ascii=False).encode())
            return

        try:
            result = analyze_ticker_cached(ticker)
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())

    def _handle_earnings_scan(self, query):
        watchlist_param = (query.get("watchlist", [""])[0]).strip()
        min_gap = float((query.get("min_gap", ["3.0"])[0]))
        min_rvol = float((query.get("min_rvol", ["2.0"])[0]))

        try:
            if watchlist_param:
                tickers = [t.strip() for t in watchlist_param.split(",") if t.strip()]
                results = scan_earnings_movers_from_watchlist(
                    tickers, min_gap_pct=min_gap, min_rvol=min_rvol
                )
            else:
                results = scan_earnings_movers(min_gap_pct=min_gap, min_rvol=min_rvol)

            payload = {
                "count": len(results),
                "min_gap_pct": min_gap,
                "min_rvol": min_rvol,
                "movers": [_mover_to_dict(m) for m in results],
            }
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
