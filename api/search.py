"""
Vercel Python Serverless Function
GET /api/search?ticker=AAPL
GET /api/search?mode=earnings_scan&watchlist=AAPL,NVDA,ANET
GET /api/search?mode=symbol_search&q=broad
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import analyze_ticker_cached  # noqa: E402
from earnings_engine import (  # noqa: E402
    scan_earnings_movers,
    scan_earnings_movers_from_watchlist,
    _mover_to_dict,
)

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

ALLOWED_EXCHANGES = {"US"}


def _finnhub_symbol_search(query):
    """Finnhub /search 엔드포인트로 티커/회사명 자동완성 검색"""
    if not FINNHUB_API_KEY:
        return {"error": "FINNHUB_API_KEY가 설정되지 않았습니다."}

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/search",
            params={"q": query, "token": FINNHUB_API_KEY},
            timeout=5,
        )
        if resp.status_code != 200:
            return {"error": f"Finnhub API 오류 ({resp.status_code}): {resp.text[:200]}"}
        data = resp.json()
    except requests.RequestException as e:
        return {"error": f"Finnhub 요청 실패: {e}"}
    except Exception as e:
        return {"error": f"검색 실패: {e}"}

    raw_results = data.get("result", []) if isinstance(data, dict) else []

    results = []
    for item in raw_results:
        symbol = item.get("symbol", "")
        desc = item.get("description", "")
        item_type = item.get("type", "")
        if not symbol or "." in symbol or ":" in symbol:
            continue
        if item_type not in ("Common Stock", "ETF", ""):
            continue
        results.append({"ticker": symbol, "name": desc})
        if len(results) >= 8:
            break

    return {"results": results}


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

        if mode == "symbol_search":
            self._handle_symbol_search(query)
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

    def _handle_symbol_search(self, query):
        q = (query.get("q", [""])[0]).strip()
        if not q or len(q) < 1:
            self.wfile.write(json.dumps({"results": []}, ensure_ascii=False).encode())
            return
        try:
            result = _finnhub_symbol_search(q)
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
