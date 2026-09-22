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
SCHWAB_PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"

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
            "close": quote.get("closePrice"),
            "net_change": quote.get("netChange"),
            "net_percent_change": quote.get("netPercentChange"),
            "trade_time": quote.get("tradeTime"),
            "quote_time": quote.get("quoteTime"),
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


def _schwab_price_history(ticker):
    """Fetch 5-minute intraday candles from Schwab for the Trade Tracker."""
    client_id = os.environ.get("SCHWAB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SCHWAB_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("SCHWAB_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        return {"error": "Schwab environment variables are missing.", "bars": []}

    token_resp = requests.post(
        SCHWAB_TOKEN_URL,
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    if token_resp.status_code != 200:
        return {"error": "Schwab token refresh failed", "status": token_resp.status_code, "bars": []}

    access_token = (token_resp.json() or {}).get("access_token")
    if not access_token:
        return {"error": "Schwab access token missing from refresh response.", "bars": []}

    resp = requests.get(
        SCHWAB_PRICE_HISTORY_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={
            "symbol": ticker.upper(),
            "periodType": "day",
            "frequencyType": "minute",
            "frequency": 5,
            "startDate": int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp() * 1000),
            "endDate": int(datetime.now(timezone.utc).timestamp() * 1000),
            "needExtendedHoursData": "false",
            "needPreviousClose": "true",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return {"error": "Schwab price history request failed", "status": resp.status_code, "detail": resp.text[:300], "bars": []}

    payload = resp.json() or {}
    bars = []
    for row in payload.get("candles") or []:
        try:
            bars.append({
                "time": int(row["datetime"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
                "vwap": float(row["close"]),
                "transactions": 0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "bars": bars,
        "previous_close": payload.get("previousClose"),
        "previous_close_date": payload.get("previousCloseDate"),
        "source": "Schwab Trader API",
        "realtime": True,
    }

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

    schwab_history = _schwab_price_history(ticker)
    all_bars = schwab_history.get("bars") or []
    bar_source = "Schwab Trader API"
    if not all_bars:
        # Fallback only if Schwab candle history is temporarily unavailable.
        all_bars = _intraday_five_minute_bars(ticker)
        bar_source = "Massive fallback"
    bars = _latest_regular_session_bars(all_bars)
    regular_all = _regular_session_bars_all(all_bars)
    latest = bars[-1] if bars else None

    closes = [float(bar["close"]) for bar in regular_all]
    hull_series = _hull_series(closes, 21)
    hull50_series = _hull_series(closes, 50)
    hull21 = hull_series[-1] if hull_series else None
    hull21_prev = hull_series[-2] if len(hull_series) > 1 else None
    hull21_slope_pct = None
    if hull21 is not None and hull21_prev not in (None, 0):
        hull21_slope_pct = (hull21 - hull21_prev) / hull21_prev * 100

    # Trade Tracker study series: HULL deviation + adaptive bands + volume/ATR regime.
    # This is a web adaptation of the user's Thinkorswim Hull_Deviation_Reentry_v3 logic.
    tracker_series = []
    true_ranges = []
    atr_series = []
    for i, bar in enumerate(regular_all):
        prev_close = closes[i - 1] if i > 0 else closes[i]
        tr = max(
            float(bar["high"]) - float(bar["low"]),
            abs(float(bar["high"]) - prev_close),
            abs(float(bar["low"]) - prev_close),
        )
        true_ranges.append(tr)
        if i == 0:
            atr_series.append(tr)
        else:
            prev_atr = atr_series[-1]
            atr_series.append((prev_atr * 13 + tr) / 14.0)

    dev_series = []
    for i, close_i in enumerate(closes):
        h21 = hull_series[i] if i < len(hull_series) else None
        dev_series.append(((close_i - h21) / h21 * 100) if h21 not in (None, 0) else None)

    start_i = max(0, len(regular_all) - 60)
    for i in range(start_i, len(regular_all)):
        h21 = hull_series[i] if i < len(hull_series) else None
        h50 = hull50_series[i] if i < len(hull50_series) else None
        dev = dev_series[i]
        valid_dev = [x for x in dev_series[max(0, i - 49):i + 1] if x is not None]
        upper = lower = None
        if len(valid_dev) >= 10:
            avg_dev = sum(valid_dev) / len(valid_dev)
            variance = sum((x - avg_dev) ** 2 for x in valid_dev) / len(valid_dev)
            stdev = variance ** 0.5
            upper = avg_dev + stdev * 2.0
            lower = avg_dev - stdev * 2.0

        slope_now = slope_prev = None
        slope_accel = False
        if i >= 6 and hull_series[i] is not None and hull_series[i - 3] is not None and hull_series[i - 6] is not None:
            slope_now = (hull_series[i] - hull_series[i - 3]) / 3.0
            slope_prev = (hull_series[i - 3] - hull_series[i - 6]) / 3.0
            slope_accel = abs(slope_now) > abs(slope_prev)

        prior_vols = [float(x["volume"]) for x in regular_all[max(0, i - 20):i]]
        vol_avg = (sum(prior_vols) / len(prior_vols)) if prior_vols else None
        vol_ratio = (float(regular_all[i]["volume"]) / vol_avg) if vol_avg and vol_avg > 0 else None

        prior_atrs = atr_series[max(0, i - 20):i]
        atr_avg = (sum(prior_atrs) / len(prior_atrs)) if prior_atrs else None
        atr_ratio = (atr_series[i] / atr_avg) if atr_avg and atr_avg > 0 else None
        atr_expanding = bool(atr_ratio is not None and atr_ratio > 1.0)

        cont_score = (1 if slope_accel else 0) + (1 if vol_ratio is not None and vol_ratio >= 1.0 else 0) + (1 if atr_expanding else 0)
        regime = "CONTINUATION" if cont_score >= 2 else "REVERSION"
        trend = "UP" if h21 is not None and h50 is not None and h21 > h50 else "DOWN" if h21 is not None and h50 is not None else "NA"

        tracker_series.append({
            "time": int(regular_all[i]["time"]),
            "close": float(regular_all[i]["close"]),
            "volume": float(regular_all[i]["volume"]),
            "hull21": h21,
            "hull50": h50,
            "dev_pct": dev,
            "upper_band": upper,
            "lower_band": lower,
            "vol_ratio": vol_ratio,
            "atr_ratio": atr_ratio,
            "slope_accelerating": slope_accel,
            "cont_score": cont_score,
            "regime": regime,
            "trend": trend,
        })

    tracker_latest = tracker_series[-1] if tracker_series else None

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
        "live_bars": regular_all[-100:],
        "latest_bar": latest,
        "bar_source": bar_source,
        "bar_realtime": bar_source == "Schwab Trader API",
        "session_vwap": _regular_session_vwap(bars),
        "hull21": hull21,
        "hull21_prev": hull21_prev,
        "hull21_slope_pct": hull21_slope_pct,
        "hull21_distance_pct": hull21_distance_pct,
        "hull21_timeframe": "5m",
        "tracker_series": tracker_series,
        "tracker_latest": tracker_latest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_mode": "live_schwab_stock_delayed_options" if live_spot is not None and bar_source == "Schwab Trader API" else "mixed_fallback",
        "delay_minutes": 0 if bar_source == "Schwab Trader API" else 15,
        "stock_delay_minutes": 0 if live_spot is not None and bar_source == "Schwab Trader API" else 15,
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



PREMARKET_GROUPS = {
    "SEMICONDUCTOR": ["NVDA","AMD","AVGO","MU","ARM","MRVL","INTC","QCOM","TSM"],
    "AI / SOFTWARE": ["PLTR","ORCL","CRM","SNOW","DDOG","CRWD","MDB","NOW"],
    "MEGA TECH": ["AAPL","MSFT","AMZN","META","GOOGL","NFLX","TSLA"],
    "POWER / AI INFRA": ["VRT","CEG","VST","GEV","ETN","BE"],
    "FINTECH / CRYPTO": ["COIN","HOOD","MSTR","SOFI","PYPL"],
    "CLOUD / DATA": ["CRWV","NBIS","DELL","HPE","ANET"],
}
PREMARKET_UNIVERSE = list(dict.fromkeys(
    ticker for names in PREMARKET_GROUPS.values() for ticker in names
))

def _ticker_sector(ticker):
    for sector, names in PREMARKET_GROUPS.items():
        if ticker in names:
            return sector
    return "OTHER"

def _finnhub_company_news(ticker, days=2):
    if not FINNHUB_API_KEY:
        return []
    end = date.today()
    start = end - timedelta(days=max(1, int(days)))
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": FINNHUB_API_KEY,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        rows = r.json() or []
        rows = sorted(rows, key=lambda x: x.get("datetime") or 0, reverse=True)
        return rows[:3]
    except Exception:
        return []

def _news_reason(headline):
    text = (headline or "").lower()
    groups = [
        ("EARNINGS", ["earnings","revenue","eps","quarter","results"]),
        ("GUIDANCE", ["guidance","outlook","forecast","raises","cuts outlook"]),
        ("CONTRACT", ["contract","award","deal","partnership","agreement","selected by"]),
        ("FDA / CLINICAL", ["fda","clinical","trial","phase 2","phase 3","approval"]),
        ("M&A", ["acquire","acquisition","merger","buyout","takeover"]),
        ("ANALYST", ["upgrade","downgrade","price target","initiates","overweight"]),
        ("OFFERING", ["offering","share sale","secondary","convertible notes"]),
        ("PRODUCT / AI", ["launch","unveil","artificial intelligence"," ai ","chip","platform"]),
    ]
    for label, words in groups:
        if any(w in text for w in words):
            return label
    return "NEWS"

def _premarket_scan(limit=16, min_gap=1.0):
    quotes = {}
    for i in range(0, len(PREMARKET_UNIVERSE), 5):
        batch = PREMARKET_UNIVERSE[i:i+5]
        result = _schwab_quotes(batch)
        if result.get("_error"):
            continue
        quotes.update(result)

    movers = []
    for ticker in PREMARKET_UNIVERSE:
        q = quotes.get(ticker) or {}
        spot = q.get("last")
        if spot is None:
            spot = q.get("mark")
        if spot is None:
            bid, ask = q.get("bid"), q.get("ask")
            if bid is not None and ask is not None:
                spot = (float(bid) + float(ask)) / 2
        prev_close = q.get("close")
        try:
            spot = float(spot)
            prev_close = float(prev_close)
        except (TypeError, ValueError):
            continue
        if prev_close <= 0:
            continue
        gap = (spot - prev_close) / prev_close * 100
        if gap < float(min_gap):
            continue
        movers.append({
            "ticker": ticker,
            "sector": _ticker_sector(ticker),
            "spot": spot,
            "prev_close": prev_close,
            "gap_pct": gap,
            "volume": q.get("volume"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "realtime": q.get("realtime"),
            "description": q.get("description"),
        })

    movers.sort(key=lambda x: x["gap_pct"], reverse=True)

    # Sector breadth from all positive premarket names in our scan universe.
    sector_stats = {}
    for sector, names in PREMARKET_GROUPS.items():
        vals = []
        for ticker in names:
            q = quotes.get(ticker) or {}
            try:
                spot = float(q.get("last") if q.get("last") is not None else q.get("mark"))
                close = float(q.get("close"))
                if close > 0:
                    vals.append((spot - close) / close * 100)
            except (TypeError, ValueError):
                continue
        positive = [v for v in vals if v >= float(min_gap)]
        sector_stats[sector] = {
            "members_checked": len(vals),
            "positive_count": len(positive),
            "avg_gap_pct": (sum(vals) / len(vals)) if vals else None,
        }

    # News lookup only for the strongest movers to keep the scan quick.
    for item in movers[:min(12, len(movers))]:
        news = _finnhub_company_news(item["ticker"], days=2)
        latest = news[0] if news else None
        sector = sector_stats.get(item["sector"]) or {}
        if latest and latest.get("headline"):
            item["reason"] = _news_reason(latest.get("headline"))
            item["headline"] = latest.get("headline")
            item["news_url"] = latest.get("url")
            item["news_time"] = latest.get("datetime")
        elif (sector.get("positive_count") or 0) >= 2:
            item["reason"] = "SECTOR MOVE"
            item["headline"] = f'{item["sector"]}: {sector.get("positive_count")} peers are also up'
        else:
            item["reason"] = "MOMENTUM / CHECK NEWS"
            item["headline"] = "No clear catalyst detected from connected news source."
        item["sector_positive_count"] = sector.get("positive_count")
        item["sector_avg_gap_pct"] = sector.get("avg_gap_pct")

    wanted = max(1, min(int(limit), 25))
    return {
        "count": min(len(movers), wanted),
        "movers": movers[:wanted],
        "min_gap_pct": float(min_gap),
        "universe_count": len(PREMARKET_UNIVERSE),
        "sector_stats": sector_stats,
        "price_source": "Schwab Trader API real-time quote",
        "news_source": "Finnhub company news when configured",
        "note": "This is a focused liquid-growth universe, not the entire US market. Premarket gain is measured versus the previous close.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _option_swing_market_candidates(limit=12):
    """Fast first-pass universe for liquid swing candidates with a relaxed fallback."""
    data = _massive_get(
        f"{MASSIVE_API_BASE}/v2/snapshot/locale/us/markets/stocks/gainers",
        {"include_otc": "false"},
    )
    rows = data.get("tickers") or []

    parsed = []
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
            dollar_volume = price * volume
            parsed.append({
                "ticker": ticker,
                "price": price,
                "change_pct": change_pct,
                "day_volume": volume,
                "dollar_volume": dollar_volume,
                "prev_day_volume": prev_volume,
                "volume_vs_prev_day": (volume / prev_volume) if prev_volume > 0 else None,
            })
        except (TypeError, ValueError):
            continue

    # Preferred swing-quality filter.
    strict = [
        x for x in parsed
        if x["price"] >= 10
        and 2 <= x["change_pct"] <= 25
        and x["day_volume"] >= 750000
        and x["dollar_volume"] >= 20_000_000
    ]

    # Broader regular-market fallback. We want enough names to study, not just
    # the cleanest institutional swing setups.
    relaxed = [
        x for x in parsed
        if x["price"] >= 5
        and 0.5 <= x["change_pct"] <= 80
        and x["day_volume"] >= 100000
        and x["dollar_volume"] >= 2_000_000
        and x not in strict
    ]

    # Final discovery bucket: keep liquid movers even if they are small-cap/event driven.
    # They are still labeled by WHY and must pass the Trade Tracker before entry.
    discovery = [
        x for x in parsed
        if x["price"] >= 5
        and x["change_pct"] >= 0.10
        and x["day_volume"] >= 10000
        and x["dollar_volume"] >= 100000
        and x not in strict
        and x not in relaxed
    ]

    strict.sort(key=lambda x: (x["change_pct"], x["dollar_volume"]), reverse=True)
    relaxed.sort(key=lambda x: (x["change_pct"], x["dollar_volume"]), reverse=True)
    discovery.sort(key=lambda x: (x["change_pct"], x["dollar_volume"]), reverse=True)

    wanted = max(1, min(int(limit), 40))
    out = (strict + relaxed + discovery)[:wanted]
    mode = "strict" if len(strict) >= min(5, wanted) else "strict+relaxed+discovery"

    # Add a plain-language reason for the regular-market scanner.
    # Prefer a recent company-news catalyst; otherwise identify the stock as a market mover.
    for item in out[:min(12, len(out))]:
        news = _finnhub_company_news(item["ticker"], days=2)
        latest = news[0] if news else None
        if latest and latest.get("headline"):
            item["reason"] = _news_reason(latest.get("headline"))
            item["headline"] = latest.get("headline")
            item["news_url"] = latest.get("url")
            item["news_time"] = latest.get("datetime")
        else:
            item["reason"] = "POPULAR / MARKET MOVER"
            item["headline"] = "Strong price/volume mover from the market scan."

    return {
        "count": len(out),
        "candidates": out,
        "source": "Massive Top Market Movers",
        "selection_mode": mode,
        "strict_count": len(strict),
        "raw_count": len(parsed),
        "filters": {
            "strict": {
                "min_price": 10,
                "min_change_pct": 2,
                "max_change_pct": 25,
                "min_day_volume": 750000,
                "min_dollar_volume": 20000000,
            },
            "fallback": {
                "min_price": 5,
                "min_change_pct": 0.5,
                "max_change_pct": 80,
                "min_day_volume": 100000,
                "min_dollar_volume": 2000000,
            },
            "discovery": {
                "min_price": 5,
                "min_change_pct": 0.10,
                "min_day_volume": 10000,
                "min_dollar_volume": 100000,
            },
        },
        "note": "Strict swing-quality candidates are preferred; a liquid fallback is used only when the movers list is unusually extreme.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _swing_reclaim_setup_payload(ticker):
    """Daily swing recovery model: low -> 21EMA reclaim -> 50MA reclaim -> volume confirmation."""
    ticker = ticker.upper()
    daily = _daily_bars(ticker, lookback_days=260)
    if len(daily) < 55:
        return {"ticker": ticker, "error": "Not enough daily history for swing reclaim analysis."}

    schwab = _schwab_quote(ticker)
    if schwab.get("error"):
        schwab = None

    closes = [float(x["close"]) for x in daily]
    highs = [float(x["high"]) for x in daily]
    lows = [float(x["low"]) for x in daily]
    vols = [float(x["volume"]) for x in daily]

    live_spot = None
    if schwab:
        live_spot = schwab.get("last") or schwab.get("mark") or schwab.get("bid") or schwab.get("ask")
    spot = float(live_spot) if live_spot is not None else closes[-1]

    ema21 = _ema(closes[-80:], 21)
    ema21_prev = _ema(closes[-81:-1], 21) if len(closes) >= 81 else None
    sma50 = _sma(closes, 50)
    sma50_prev = _sma(closes[:-1], 50)
    sma200 = _sma(closes, 200)

    lookback = min(30, len(daily))
    recent = daily[-lookback:]
    low_bar = min(recent, key=lambda x: float(x["low"]))
    low_price = float(low_bar["low"])
    low_index = daily.index(low_bar)
    days_from_low = max(0, len(daily) - 1 - low_index)
    rise_from_low_pct = ((spot - low_price) / low_price * 100) if low_price > 0 else None

    prev_close = closes[-2]
    day_change_pct = ((spot - prev_close) / prev_close * 100) if prev_close else None

    avg20vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    latest_volume = vols[-1]
    volume_ratio = (latest_volume / avg20vol) if avg20vol and avg20vol > 0 else None

    was_below_21 = any(float(x["close"]) < (_ema([float(y["close"]) for y in daily[:i+1]][-80:], 21) or float(x["close"]))
                       for i, x in enumerate(daily[-6:-1], start=len(daily)-6))
    reclaim21 = bool(ema21 is not None and spot >= ema21 and (closes[-2] < ema21 or was_below_21))
    reclaim50 = bool(sma50 is not None and spot >= sma50 and closes[-2] < sma50)
    above21 = bool(ema21 is not None and spot >= ema21)
    above50 = bool(sma50 is not None and spot >= sma50)
    ema21_rising = bool(ema21_prev is not None and ema21 > ema21_prev)
    sma50_rising = bool(sma50_prev is not None and sma50 > sma50_prev)

    # Stock-level recovery confirmation inspired by an FTD concept.
    # This is intentionally labeled "FTD-style" because classic O'Neil FTD is a market-index concept.
    ftd_style = bool(
        day_change_pct is not None and day_change_pct >= 1.5 and
        volume_ratio is not None and volume_ratio >= 1.3 and
        spot > prev_close and
        days_from_low >= 2
    )

    recent_high = max(highs[-20:])
    from_20d_high_pct = ((spot - recent_high) / recent_high * 100) if recent_high else None

    score = 0
    if days_from_low <= 10:
        score += 15
    if rise_from_low_pct is not None and 3 <= rise_from_low_pct <= 20:
        score += 15
    if above21:
        score += 15
    if above50:
        score += 20
    if ema21_rising:
        score += 10
    if volume_ratio is not None and volume_ratio >= 1.3:
        score += 10
    if ftd_style:
        score += 15

    if ftd_style and above21 and above50:
        stage = "SWING READY"
    elif reclaim21 and reclaim50:
        stage = "21+50 RECLAIM"
    elif above21 and reclaim50:
        stage = "50MA RECLAIM"
    elif reclaim21 or (above21 and not above50):
        stage = "21EMA RECLAIM"
    elif days_from_low <= 7 and rise_from_low_pct is not None and rise_from_low_pct > 0:
        stage = "LOW CONFIRMED"
    else:
        stage = "BOTTOM WATCH"

    return {
        "ticker": ticker,
        "spot": spot,
        "schwab_quote": schwab,
        "stage": stage,
        "score": score,
        "low_price": low_price,
        "days_from_low": days_from_low,
        "rise_from_low_pct": rise_from_low_pct,
        "day_change_pct": day_change_pct,
        "ema21": ema21,
        "sma50": sma50,
        "sma200": sma200,
        "above21": above21,
        "above50": above50,
        "reclaim21": reclaim21,
        "reclaim50": reclaim50,
        "ema21_rising": ema21_rising,
        "sma50_rising": sma50_rising,
        "volume_ratio": volume_ratio,
        "ftd_style": ftd_style,
        "from_20d_high_pct": from_20d_high_pct,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _swing_reclaim_candidates(limit=8):
    """Auto scan for depressed/recovering swing candidates, excluding already-extended spikes."""
    first_pass = _option_swing_market_candidates(limit=max(16, min(20, int(limit) * 3)))
    results = []

    for row in first_pass.get("candidates") or []:
        ticker = row.get("ticker")
        if not ticker:
            continue
        try:
            item = _swing_reclaim_setup_payload(ticker)
            if item.get("error"):
                continue

            rise = item.get("rise_from_low_pct")
            from_high = item.get("from_20d_high_pct")
            days_low = item.get("days_from_low")

            # "Depressed but recovering" guardrails:
            # - not already hundreds of percent off the low
            # - still meaningfully below the recent high, or only just reclaiming it
            # - low should be reasonably recent
            if rise is None or not (2 <= rise <= 30):
                continue
            if days_low is None or days_low > 20:
                continue
            if from_high is not None and not (-30 <= from_high <= 2):
                continue

            results.append(item)
        except Exception:
            continue

    stage_rank = {
        "SWING READY": 6,
        "21+50 RECLAIM": 5,
        "50MA RECLAIM": 4,
        "21EMA RECLAIM": 3,
        "LOW CONFIRMED": 2,
        "BOTTOM WATCH": 1,
    }
    results.sort(
        key=lambda x: (
            stage_rank.get(x.get("stage"), 0),
            x.get("score") or 0,
            -(x.get("rise_from_low_pct") or 999),
        ),
        reverse=True,
    )

    wanted = max(1, min(int(limit), 12))
    return {
        "count": min(len(results), wanted),
        "candidates": results[:wanted],
        "source": "Liquid risers first-pass + daily depressed/reclaim analysis",
        "filters": {
            "rise_from_low_pct": "2 to 30",
            "days_from_low_max": 20,
            "from_20d_high_pct": "-30 to +2",
        },
        "note": "Extended spikes are excluded; this scan focuses on depressed stocks recovering toward/through 21EMA and 50MA.",
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

        if mode == "premarket_scan":
            try:
                limit = int((query.get("limit", ["16"])[0]))
                min_gap = float((query.get("min_gap", ["1.0"])[0]))
                result = _premarket_scan(limit=limit, min_gap=min_gap)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
            return

        if mode == "option_swing_candidates":
            try:
                limit = int((query.get("limit", ["12"])[0]))
                result = _option_swing_market_candidates(limit=limit)
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
            return

        if mode == "swing_reclaim_candidates":
            try:
                limit = int((query.get("limit", ["8"])[0]))
                result = _swing_reclaim_candidates(limit=limit)
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

        if mode == "schwab_intraday":
            ticker = (query.get("ticker", [""])[0]).strip().upper()
            if not ticker or len(ticker) > 10 or not ticker.replace("-", "").replace(".", "").isalnum():
                self.wfile.write(json.dumps({"error": "올바른 미국 주식 티커를 입력하세요."}, ensure_ascii=False).encode())
                return
            try:
                hist = _schwab_price_history(ticker)
                if hist.get("error"):
                    self.wfile.write(json.dumps(hist, ensure_ascii=False).encode())
                    return
                bars = _latest_regular_session_bars(hist.get("bars") or [])
                self.wfile.write(json.dumps({
                    "ticker": ticker,
                    "bars": bars[-120:],
                    "source": hist.get("source"),
                    "realtime": hist.get("realtime"),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e), "ticker": ticker}, ensure_ascii=False).encode())
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

        if mode == "swing_reclaim_setup":
            try:
                result = _swing_reclaim_setup_payload(ticker)
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
