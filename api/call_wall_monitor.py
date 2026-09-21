"""Call Wall intraday monitor (beta).

Returns the existing GEX wall levels plus recent 5-minute underlying bars.
The current Massive Options Starter feed is delayed; option-side Ask/Bid/Mid
flow is intentionally entered in the browser during the first validation run.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from datetime import date, timedelta, datetime, timezone
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import MASSIVE_API_BASE, _massive_get, analyze_ticker_cached  # noqa: E402


def _recent_five_minute_bars(ticker: str) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=5)
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/range/5/minute/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    rows = data.get("results") or []
    bars = []
    for row in rows[-80:]:
        try:
            bars.append({
                "time": int(row["t"]),
                "open": float(row["o"]),
                "high": float(row["h"]),
                "low": float(row["l"]),
                "close": float(row["c"]),
                "volume": float(row.get("v") or 0),
                "vwap": float(row.get("vw") or row["c"]),
                "transactions": int(row.get("n") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def _session_vwap(bars: list[dict]) -> float | None:
    if not bars:
        return None
    latest_day = datetime.fromtimestamp(bars[-1]["time"] / 1000, timezone.utc).date()
    same_day = [
        bar for bar in bars
        if datetime.fromtimestamp(bar["time"] / 1000, timezone.utc).date() == latest_day
    ]
    total_volume = sum(bar["volume"] for bar in same_day)
    if total_volume <= 0:
        return None
    return sum(bar["vwap"] * bar["volume"] for bar in same_day) / total_volume


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        ticker = (query.get("ticker", [""])[0]).strip().upper()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not ticker or not ticker.replace("-", "").isalnum() or len(ticker) > 10:
            self.wfile.write(json.dumps({"error": "올바른 미국 주식 티커를 입력하세요."}, ensure_ascii=False).encode())
            return

        try:
            gex = analyze_ticker_cached(ticker, ttl=60, skip_stage=True)
            bars_error = None
            try:
                bars = _recent_five_minute_bars(ticker)
            except Exception as exc:
                # Keep the wall/spot portion usable if the account does not yet
                # include intraday aggregate access.
                bars = []
                bars_error = str(exc)
            latest = bars[-1] if bars else None
            generated_at = datetime.now(timezone.utc).isoformat()
            payload = {
                "ticker": ticker,
                "spot": gex.get("spot"),
                "call_wall": gex.get("call_wall"),
                "put_wall": gex.get("put_wall"),
                "gamma_flip": gex.get("gamma_flip"),
                "expiry_used": gex.get("expiry_used"),
                "bars": bars[-12:],
                "latest_bar": latest,
                "session_vwap": _session_vwap(bars),
                "generated_at": generated_at,
                "data_mode": "delayed_beta",
                "options_delay_minutes": 15,
                "flow_source": "manual",
                "is_stale_price": bool(gex.get("is_stale_price")),
                "bars_error": bars_error,
            }
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())
        except Exception as exc:
            self.wfile.write(json.dumps({
                "error": str(exc),
                "ticker": ticker,
                "data_mode": "delayed_beta",
            }, ensure_ascii=False).encode())
