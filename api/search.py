"""
Vercel Python Serverless Function
GET /api/search?ticker=AAPL
GET /api/search?mode=earnings_scan&watchlist=AAPL,NVDA,ANET
GET /api/search?mode=symbol_search&q=broad
GET /api/search?mode=fear_greed
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import date, datetime, timedelta, timezone
import json
import sys
import os
import requests
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from options_engine import (  # noqa: E402
    MASSIVE_API_BASE,
    _massive_get,
    analyze_ticker_cached,
    quote_paper_option_contracts,
    recommend_paper_option_contracts,
)
from earnings_engine import (  # noqa: E402
    scan_earnings_movers,
    scan_earnings_movers_from_watchlist,
    _mover_to_dict,
)

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

ALLOWED_EXCHANGES = {"US"}

CNN_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
EASTERN = ZoneInfo("America/New_York")


def _intraday_five_minute_bars(ticker):
    """Return delayed 5-minute stock aggregates from the existing Stock API."""
    end = date.today()
    start = end - timedelta(days=7)
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/range/5/minute/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    bars = []
    for row in data.get("results") or []:
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


def _regular_session_vwap(bars):
    """Volume-weight the returned 5-minute VWAP values for the latest ET session."""
    if not bars:
        return None
    latest_day = datetime.fromtimestamp(bars[-1]["time"] / 1000, timezone.utc).astimezone(EASTERN).date()
    session = []
    for bar in bars:
        stamp = datetime.fromtimestamp(bar["time"] / 1000, timezone.utc).astimezone(EASTERN)
        after_open = stamp.hour > 9 or (stamp.hour == 9 and stamp.minute >= 30)
        if stamp.date() == latest_day and after_open and stamp.hour < 16:
            session.append(bar)
    total_volume = sum(bar["volume"] for bar in session)
    if total_volume <= 0:
        return None
    return sum(bar["vwap"] * bar["volume"] for bar in session) / total_volume



def _regular_session_bars_all(bars):
    """Keep all regular-hours bars across the fetched window."""
    regular = []
    for bar in bars or []:
        stamp = datetime.fromtimestamp(bar["time"] / 1000, timezone.utc).astimezone(EASTERN)
        after_open = stamp.hour > 9 or (stamp.hour == 9 and stamp.minute >= 30)
        if after_open and stamp.hour < 16:
            regular.append(bar)
    return regular


def _wma(values, period):
    """Weighted moving average for the last period values."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    weights = list(range(1, period + 1))
    denom = sum(weights)
    return sum(v * w for v, w in zip(window, weights)) / denom


def _hull_series(values, period=21):
    """Return HMA series aligned to values; None until enough history exists."""
    if not values:
        return []
    half = max(1, period // 2)
    sqrt_p = max(1, int(period ** 0.5))
    raw = []
    out = [None] * len(values)
    for i in range(len(values)):
        seq = values[: i + 1]
        w_full = _wma(seq, period)
        w_half = _wma(seq, half)
        raw.append(None if w_full is None or w_half is None else 2 * w_half - w_full)
        valid_raw = [x for x in raw if x is not None]
        if len(valid_raw) >= sqrt_p:
            out[i] = _wma(valid_raw, sqrt_p)
    return out


def _latest_regular_session_bars(bars):
    """Keep regular-hours bars from the most recent ET trading date only."""
    if not bars:
        return []
    regular = []
    for bar in bars:
        stamp = datetime.fromtimestamp(bar["time"] / 1000, timezone.utc).astimezone(EASTERN)
        after_open = stamp.hour > 9 or (stamp.hour == 9 and stamp.minute >= 30)
        if after_open and stamp.hour < 16:
            regular.append((stamp.date(), bar))
    if not regular:
        return []
    latest_day = regular[-1][0]
    return [bar for trading_day, bar in regular if trading_day == latest_day]


def _call_wall_monitor_payload(ticker):
    gex = analyze_ticker_cached(ticker, ttl=300, skip_stage=True)
    all_bars = _intraday_five_minute_bars(ticker)
    bars = _latest_regular_session_bars(all_bars)
    regular_all = _regular_session_bars_all(all_bars)
    latest = bars[-1] if bars else None

    closes = [float(bar["close"]) for bar in regular_all]
    hull_series = _hull_series(closes, 21)
    hull21 = hull_series[-1] if hull_series else None
    hull21_prev = hull_series[-2] if len(hull_series) > 1 else None
    hull21_slope_pct = None
    if hull21 is not None and hull21_prev not in (None, 0):
        hull21_slope_pct = (hull21 - hull21_prev) / hull21_prev * 100

    close = float(latest["close"]) if latest else None
    hull21_distance_pct = None
    if close is not None and hull21 not in (None, 0):
        hull21_distance_pct = (close - hull21) / hull21 * 100

    return {
        "ticker": ticker.upper(),
        "spot": gex.get("spot"),
        "call_wall": gex.get("call_wall"),
        "put_wall": gex.get("put_wall"),
        "gamma_flip": gex.get("gamma_flip"),
        "expiry_used": gex.get("expiry_used"),
        "bars": bars[-12:],
        "latest_bar": latest,
        "session_vwap": _regular_session_vwap(bars),
        "hull21": hull21,
        "hull21_prev": hull21_prev,
        "hull21_slope_pct": hull21_slope_pct,
        "hull21_distance_pct": hull21_distance_pct,
        "hull21_timeframe": "5m",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_mode": "15_minute_delayed",
        "delay_minutes": 15,
        "flow_source": "optional_manual_confirmation",
        "is_stale_price": bool(gex.get("is_stale_price")),
    }


def _fetch_cnn_fear_greed():
    """CNN 공식 Fear & Greed 피드에서 현재 점수만 작게 정규화한다."""
    try:
        resp = requests.get(
            CNN_FEAR_GREED_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.cnn.com/markets/fear-and-greed",
                "Origin": "https://www.cnn.com",
            },
            timeout=8,
        )
        resp.raise_for_status()
        current = (resp.json() or {}).get("fear_and_greed") or {}
        score = current.get("score")
        if score is None:
            raise ValueError("CNN 응답에 score가 없습니다.")
        return {
            "score": round(float(score), 1),
            "rating": str(current.get("rating") or "").lower(),
            "timestamp": current.get("timestamp"),
            "previous_close": current.get("previous_close"),
            "source": "CNN Fear & Greed Index",
        }
    except Exception as exc:
        return {"error": f"CNN Fear & Greed 조회 실패: {exc}"}


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
        view = (query.get("view", [""])[0]).strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        if mode == "fear_greed":
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=900")
        elif view == "summary":
            self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=900")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if mode == "earnings_scan":
            self._handle_earnings_scan(query)
            return

        if mode == "symbol_search":
            self._handle_symbol_search(query)
            return

        if mode == "fear_greed":
            self.wfile.write(json.dumps(_fetch_cnn_fear_greed(), ensure_ascii=False).encode())
            return

        if mode == "config":
            self.wfile.write(json.dumps({
                "supabaseUrl": os.environ.get("SUPABASE_URL", ""),
                "supabaseAnonKey": os.environ.get("SUPABASE_ANON_KEY", ""),
            }).encode("utf-8"))
            return

        ticker = (query.get("ticker", [""])[0]).strip()
        if not ticker:
            self.wfile.write(json.dumps({"error": "ticker 파라미터가 필요합니다. 예: /api/search?ticker=AAPL"}, ensure_ascii=False).encode())
            return

        if mode == "paper_options":
            try:
                result = recommend_paper_option_contracts(ticker)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
            return

        if mode == "paper_option_quotes":
            try:
                contracts = [item.strip() for item in (query.get("contracts", [""])[0]).split(",") if item.strip()]
                result = quote_paper_option_contracts(ticker, contracts)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
            return

        if mode == "call_wall_monitor":
            try:
                result = _call_wall_monitor_payload(ticker)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
            return

        try:
            result = analyze_ticker_cached(ticker, ttl=300 if view == "summary" else 90, skip_stage=view == "summary")
            if view == "summary":
                keep = {
                    "ticker", "spot", "is_stale_price", "price_change_pct",
                    "expiry_used", "generated_at", "call_wall", "put_wall",
                    "gamma_flip", "net_gex_total", "regime",
                }
                result = {key: value for key, value in result.items() if key in keep}
                result["_summary"] = True
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
