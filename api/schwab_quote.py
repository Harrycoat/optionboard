"""Private Schwab quote bridge for GEXOption.

Reads Schwab credentials from Vercel environment variables, refreshes an
access token server-side, and returns a minimal quote payload.

Required environment variables:
- SCHWAB_CLIENT_ID
- SCHWAB_CLIENT_SECRET
- SCHWAB_REFRESH_TOKEN
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import json
import os
import base64
import requests

TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"
ALLOWED_TICKERS = {"NVDA", "AMZN"}


def _json(handler, status, payload):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _refresh_access_token():
    client_id = os.environ.get("SCHWAB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("SCHWAB_REFRESH_TOKEN", "").strip()

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "Missing SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET, or SCHWAB_REFRESH_TOKEN."
        )

    basic = base64.b64encode(
        f"{client_id}:{client_secret}".encode("utf-8")
    ).decode("ascii")

    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(
            f"Schwab token refresh failed ({response.status_code}): {response.text}"
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Schwab token refresh response did not include access_token.")
    return access_token


def _fetch_quote(ticker):
    access_token = _refresh_access_token()
    response = requests.get(
        QUOTES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={
            "symbols": ticker,
            "fields": "quote,reference",
            "indicative": "false",
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(
            f"Schwab quote request failed ({response.status_code}): {response.text}"
        )

    data = response.json()
    row = data.get(ticker) or {}
    quote = row.get("quote") or {}
    reference = row.get("reference") or {}

    return {
        "ticker": ticker,
        "bid": quote.get("bidPrice"),
        "ask": quote.get("askPrice"),
        "last": quote.get("lastPrice"),
        "mark": quote.get("mark"),
        "volume": quote.get("totalVolume"),
        "realtime": row.get("realtime"),
        "description": reference.get("description"),
        "source": "Schwab Trader API",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        ticker = (query.get("ticker", ["NVDA"])[0] or "NVDA").strip().upper()

        if ticker not in ALLOWED_TICKERS:
            _json(
                self,
                400,
                {
                    "error": "This test endpoint currently supports only NVDA and AMZN.",
                    "allowed": sorted(ALLOWED_TICKERS),
                },
            )
            return

        try:
            _json(self, 200, _fetch_quote(ticker))
        except Exception as exc:
            _json(
                self,
                500,
                {
                    "error": str(exc),
                    "ticker": ticker,
                    "source": "Schwab Trader API",
                },
            )
