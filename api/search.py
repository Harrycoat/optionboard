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


SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"

def _schwab_quotes(tickers):
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    if not tickers:
        return {}

    client_id = os.environ.get("SCHWAB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("SCHWAB_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        return {"_error": "Schwab environment variables are missing."}

    token_resp = requests.post(
        SCHWAB_TOKEN_URL,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if token_resp.status_code != 200:
        return {
            "_error": "Schwab token refresh failed",
            "_status": token_resp.status_code,
            "_detail": token_resp.text[:300],
        }

    access_token = (token_resp.json() or {}).get("access_token")
    if not access_token:
        return {"_error": "Schwab access token missing from refresh response."}

    quote_resp = requests.get(
        SCHWAB_QUOTES_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={
            "symbols": ",".join(tickers),
            "fields": "quote,reference",
            "indicative": "false",
        },
        timeout=15,
    )
    if quote_resp.status_code != 200:
        return {
            "_error": "Schwab quote request failed",
            "_status": quote_resp.status_code,
            "_detail": quote_resp.text[:300],
        }

    raw = quote_resp.json() or {}
    out = {}
    for ticker in tickers:
        row = raw.get(ticker) or {}
        quote = row.get("quote") or {}
        reference = row.get("reference") or {}
        out[ticker] = {
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
    return out


def _schwab_quote(ticker):
    result = _schwab_quotes([ticker])
    if result.get("_error"):
        return {
            "error": result.get("_error"),
            "status": result.get("_status"),
            "detail": result.get("_detail"),
        }
    return result.get(ticker.upper()) or {"error": "No Schwab quote returned."}

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

    schwab = _schwab_quote(ticker.upper())
    if schwab.get("error"):
        schwab = None

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

    live_spot = None
    if schwab:
        live_spot = schwab.get("last") or schwab.get("mark") or schwab.get("bid") or schwab.get("ask")

    return {
        "ticker": ticker.upper(),
        "spot": live_spot if live_spot is not None else gex.get("spot"),
        "gex_spot": gex.get("spot"),
        "stock_source": "Schwab Trader API" if live_spot is not None else "Massive delayed",
        "stock_realtime": bool(schwab and schwab.get("realtime") is True),
        "schwab_quote": schwab,
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
        "data_mode": "live_stock_delayed_options" if live_spot is not None else "15_minute_delayed",
        "delay_minutes": 15,
        "stock_delay_minutes": 0 if live_spot is not None else 15,
        "options_delay_minutes": 15,
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


def _daily_bars(ticker, lookback_days=120):
    """Daily stock aggregates for swing structure checks."""
    end = date.today()
    start = end - timedelta(days=lookback_days)
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 500},
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
            })
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def _ema(values, period):
    if not values:
        return None
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for item in values[1:]:
        value = alpha * float(item) + (1 - alpha) * value
    return value


def _sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _aggregate_15m(session_bars):
    """Combine sequential 5-minute regular-session bars into 15-minute bars."""
    out = []
    chunk = []
    for bar in session_bars or []:
        chunk.append(bar)
        if len(chunk) == 3:
            out.append({
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(x["high"] for x in chunk),
                "low": min(x["low"] for x in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(x["volume"] for x in chunk),
            })
            chunk = []
    return out


def _range_pct(bar):
    close = float(bar.get("close") or 0)
    if close <= 0:
        return None
    return (float(bar.get("high") or close) - float(bar.get("low") or close)) / close * 100


def _five_min_minervini(bars):
    """Intraday contraction/pivot heuristic inspired by VCP structure."""
    sample = list(bars or [])[-12:]
    if len(sample) < 9:
        return {
            "stage": "WAIT",
            "score": 0,
            "pivot": None,
            "pivot_distance_pct": None,
            "contraction": False,
            "volume_dryup": False,
        }

    thirds = [sample[-12:-8], sample[-8:-4], sample[-4:]]
    ranges = []
    for group in thirds:
        vals = [_range_pct(x) for x in group]
        vals = [x for x in vals if x is not None]
        ranges.append(sum(vals) / len(vals) if vals else None)

    contraction = all(x is not None for x in ranges) and ranges[2] < ranges[1] < ranges[0]
    vols = [float(x.get("volume") or 0) for x in sample]
    early_vol = sum(vols[:4]) / 4 if len(vols) >= 4 else 0
    late_vol = sum(vols[-4:]) / 4 if len(vols) >= 4 else 0
    volume_dryup = early_vol > 0 and late_vol < early_vol * 0.85

    pivot = max(float(x["high"]) for x in sample[-6:-1]) if len(sample) >= 6 else None
    close = float(sample[-1]["close"])
    pivot_distance_pct = ((pivot - close) / pivot * 100) if pivot else None

    latest_vol = vols[-1]
    prev_avg = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 0
    breakout_volume = prev_avg > 0 and latest_vol >= prev_avg * 1.5
    breakout = bool(pivot and close > pivot and breakout_volume)

    score = 0
    score += 35 if contraction else 0
    score += 20 if volume_dryup else 0
    score += 20 if pivot_distance_pct is not None and -0.5 <= pivot_distance_pct <= 1.5 else 0
    score += 25 if breakout else 0

    if breakout:
        stage = "BREAKOUT"
    elif contraction and pivot_distance_pct is not None and pivot_distance_pct <= 1.5:
        stage = "PIVOT READY"
    elif contraction:
        stage = "T3"
    elif volume_dryup:
        stage = "T2"
    else:
        stage = "T1"

    return {
        "stage": stage,
        "score": score,
        "pivot": pivot,
        "pivot_distance_pct": pivot_distance_pct,
        "contraction": contraction,
        "volume_dryup": volume_dryup,
        "breakout_volume": breakout_volume,
        "range_sequence_pct": ranges,
    }


def _option_swing_market_candidates(limit=12):
    """Use Massive top movers as a fast first-pass universe for today's trading candidates."""
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/snapshot/locale/us/markets/stocks/gainers",
        {"include_otc": "false"},
    )
    rows = data.get("tickers") or []
    out = []
    for row in rows:
        try:
            ticker = str(row.get("ticker") or "").upper().strip()
            day = row.get("day") or {}
            prev = row.get("prevDay") or {}
            price = row.get("lastTrade", {}).get("p") or day.get("c") or row.get("min", {}).get("c")
            change_pct = row.get("todaysChangePerc")
            volume = day.get("v") or 0
            prev_volume = prev.get("v") or 0
            if not ticker or price is None or change_pct is None:
                continue
            price = float(price)
            change_pct = float(change_pct)
            volume = float(volume or 0)
            prev_volume = float(prev_volume or 0)
            if price < 5 or volume < 250000:
                continue
            out.append({
                "ticker": ticker,
                "price": price,
                "change_pct": change_pct,
                "day_volume": volume,
                "prev_day_volume": prev_volume,
                "volume_vs_prev_day": (volume / prev_volume) if prev_volume > 0 else None,
            })
        except (TypeError, ValueError):
            continue
        if len(out) >= max(1, min(int(limit), 20)):
            break
    return {
        "count": len(out),
        "candidates": out,
        "source": "Massive Top Market Movers",
        "note": "Fast first-pass universe; detailed 5m/15m setup is calculated separately.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _option_swing_setup_payload(ticker):
    ticker = ticker.upper()
    all_5m = _intraday_five_minute_bars(ticker)
    session_5m = _latest_regular_session_bars(all_5m)
    bars_15m = _aggregate_15m(session_5m)
    daily = _daily_bars(ticker)

    schwab = _schwab_quote(ticker)
    if schwab.get("error"):
        schwab = None
    live_spot = None
    if schwab:
        live_spot = schwab.get("last") or schwab.get("mark") or schwab.get("bid") or schwab.get("ask")

    spot = float(live_spot) if live_spot is not None else (float(session_5m[-1]["close"]) if session_5m else None)

    closes = [float(x["close"]) for x in daily]
    ema21 = _ema(closes[-60:], 21) if closes else None
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    prev_close = closes[-2] if len(closes) >= 2 else None
    day_change_pct = ((spot - prev_close) / prev_close * 100) if spot is not None and prev_close else None

    pullback_days = 0
    for i in range(len(daily) - 1, max(-1, len(daily) - 4), -1):
        if i > 0 and daily[i]["close"] < daily[i - 1]["close"]:
            pullback_days += 1
        else:
            break

    avg10vol = None
    if len(daily) >= 11:
        avg10vol = sum(float(x["volume"]) for x in daily[-11:-1]) / 10
    latest_daily_vol = float(daily[-1]["volume"]) if daily else None
    volume_dryup_daily = bool(avg10vol and latest_daily_vol is not None and latest_daily_vol < avg10vol * 0.85)

    recent_high = max((float(x["high"]) for x in daily[-20:]), default=None)
    from_high_pct = ((spot - recent_high) / recent_high * 100) if spot is not None and recent_high else None
    trend_ok = bool(
        spot is not None and
        (ema21 is None or spot >= ema21) and
        (sma50 is None or spot >= sma50)
    )
    pullback_leader = bool(pullback_days in (2, 3) and trend_ok and volume_dryup_daily)

    latest_15 = bars_15m[-1] if bars_15m else None
    prior_15 = bars_15m[-5:-1] if len(bars_15m) >= 5 else []
    prior_15_high = max((float(x["high"]) for x in prior_15), default=None)
    avg_15_vol = (sum(float(x["volume"]) for x in prior_15) / len(prior_15)) if prior_15 else None
    vol15_ratio = (
        float(latest_15["volume"]) / avg_15_vol
        if latest_15 and avg_15_vol and avg_15_vol > 0
        else None
    )
    breakout15 = bool(
        latest_15 and prior_15_high and
        float(latest_15["close"]) > prior_15_high and
        vol15_ratio is not None and vol15_ratio >= 1.5
    )

    micro = _five_min_minervini(session_5m)

    latest5 = session_5m[-1] if session_5m else None
    prev5 = session_5m[-6:-1] if len(session_5m) >= 6 else []
    avg5 = (sum(float(x["volume"]) for x in prev5) / len(prev5)) if prev5 else None
    vol5_ratio = (
        float(latest5["volume"]) / avg5
        if latest5 and avg5 and avg5 > 0
        else None
    )

    today_leader = bool(
        day_change_pct is not None and day_change_pct >= 2.0 and
        vol5_ratio is not None and vol5_ratio >= 1.3
    )

    return {
        "ticker": ticker,
        "spot": spot,
        "schwab_quote": schwab,
        "day_change_pct": day_change_pct,
        "today_leader": today_leader,
        "pullback_leader": pullback_leader,
        "pullback_days": pullback_days,
        "daily_volume_dryup": volume_dryup_daily,
        "from_20d_high_pct": from_high_pct,
        "ema21": ema21,
        "sma50": sma50,
        "sma200": sma200,
        "trend_ok": trend_ok,
        "breakout15": breakout15,
        "breakout15_level": prior_15_high,
        "volume15_ratio": vol15_ratio,
        "volume5_ratio": vol5_ratio,
        "minervini5m": micro,
        "latest_5m": latest5,
        "latest_15m": latest_15,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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

        if mode == "option_swing_candidates":
            try:
                limit = int((query.get("limit", ["12"])[0]))
                result = _option_swing_market_candidates(limit=limit)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
            return

        if mode == "schwab_quote":
            symbols_param = (query.get("symbols", [""])[0]).strip()
            if symbols_param:
                tickers = []
                for raw in symbols_param.split(","):
                    t = raw.strip().upper()
                    if t and len(t) <= 10 and t.replace("-", "").replace(".", "").isalnum():
                        tickers.append(t)
                tickers = list(dict.fromkeys(tickers))[:5]
                if not tickers:
                    self.wfile.write(json.dumps({"error": "올바른 미국 주식 티커가 필요합니다."}, ensure_ascii=False).encode())
                    return
                result = _schwab_quotes(tickers)
                if result.get("_error"):
                    self.wfile.write(json.dumps({"error": result.get("_error"), "status": result.get("_status"), "detail": result.get("_detail")}, ensure_ascii=False).encode())
                    return
                self.wfile.write(json.dumps({"quotes": result}, ensure_ascii=False).encode())
                return

            ticker = (query.get("ticker", [""])[0]).strip().upper()
            if not ticker or len(ticker) > 10 or not ticker.replace("-", "").replace(".", "").isalnum():
                self.wfile.write(json.dumps({
                    "error": "올바른 미국 주식 티커를 입력하세요."
                }, ensure_ascii=False).encode())
                return
            self.wfile.write(json.dumps(_schwab_quote(ticker), ensure_ascii=False).encode())
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

        if mode == "option_swing_setup":
            try:
                result = _option_swing_setup_payload(ticker)
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
