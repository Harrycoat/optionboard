"""GEXOption Swing Volume Heatmap beta.
Daily timeframe stock-discovery engine using Schwab daily candles.
NOTE: Schwab daily candles expose total volume, not aggressor-side trade volume.
Therefore this beta uses candle-position weighted volume pressure and labels it
as an ESTIMATE. It must not be presented as true bid/ask Volume Delta.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import base64, json, os, time
import requests

TOKEN_URL="https://api.schwabapi.com/v1/oauth/token"
HISTORY_URL="https://api.schwabapi.com/marketdata/v1/pricehistory"

UNIVERSE={
"AI / Mega Tech":["NVDA","MSFT","GOOGL","AMZN","META","AAPL","ORCL"],
"AI Semiconductor":["NVDA","AMD","AVGO","ARM","INTC","MU"],
"AI Networking":["ANET","CRDO","MRVL","ALAB","LITE","CIEN"],
"Memory / Storage":["MU","SNDK","WDC","STX"],
"AI Server / Hardware":["DELL","SMCI","HPE","VRT","APH"],
"AI Cloud / Data Center":["NBIS","IREN","CIFR","CLSK","VRT","ETN","GEV"],
"Cybersecurity":["CRWD","PANW","FTNT","OKTA","ZS","NET"],
"Robotics / Automation":["TSLA","ISRG","ROK"],
"Nuclear / Power":["CEG","VST","CCJ","LEU","OKLO","SMR"],
"Space / Defense":["RKLB","PLTR","LMT","RTX"],
"Healthcare":["LLY","TMO","VRTX","REGN","AMGN"],
"Financial":["JPM","BAC","WFC","GS","MS","COIN"]
}

def out(h,status,payload):
    h.send_response(status); h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Cache-Control","no-store"); h.send_header("Access-Control-Allow-Origin","*"); h.end_headers()
    h.wfile.write(json.dumps(payload,ensure_ascii=False).encode())

def token():
    cid=os.environ.get("SCHWAB_CLIENT_ID","").strip(); sec=os.environ.get("SCHWAB_CLIENT_SECRET","").strip()
    ref=os.environ.get("SCHWAB_REFRESH_TOKEN","").strip()
    if not cid or not sec or not ref: raise RuntimeError("Schwab environment variables are missing.")
    basic=base64.b64encode(f"{cid}:{sec}".encode()).decode()
    r=requests.post(TOKEN_URL,headers={"Authorization":f"Basic {basic}","Content-Type":"application/x-www-form-urlencoded"},
                    data={"grant_type":"refresh_token","refresh_token":ref},timeout=20)
    if not r.ok: raise RuntimeError(f"Schwab token refresh failed ({r.status_code})")
    return r.json()["access_token"]

def candles(sym,access):
    end=int(time.time()*1000); start=end-(120*86400000)
    r=requests.get(HISTORY_URL,headers={"Authorization":f"Bearer {access}","Accept":"application/json"},
      params={"symbol":sym,"periodType":"year","period":1,"frequencyType":"daily","frequency":1,
              "startDate":start,"endDate":end,"needExtendedHoursData":"false","needPreviousClose":"true"},timeout=20)
    if not r.ok: raise RuntimeError(f"{sym}: price history {r.status_code}")
    return r.json().get("candles") or []

def pressure(c):
    hi=float(c.get("high") or 0); lo=float(c.get("low") or 0); close=float(c.get("close") or 0); vol=float(c.get("volume") or 0)
    rng=max(hi-lo,1e-9)
    # Close Location Value [-1,+1] * total volume = estimated daily pressure.
    clv=((close-lo)-(hi-close))/rng
    return clv*vol

def _ema(values, length):
    alpha = 2.0 / (length + 1.0)
    out = []
    ema = None
    for value in values:
        ema = value if ema is None else (alpha * value + (1.0 - alpha) * ema)
        out.append(ema)
    return out

def _wma(values, length):
    if len(values) < length:
        return None
    weights = list(range(1, length + 1))
    denom = sum(weights)
    window = values[-length:]
    return sum(v * w for v, w in zip(window, weights)) / denom

def _hma_at(values, length=21):
    # Hull Moving Average = WMA(2*WMA(n/2)-WMA(n), sqrt(n))
    half = max(1, length // 2)
    root = max(1, int(length ** 0.5))
    if len(values) < length + root - 1:
        return None
    raw = []
    start = length - 1
    for end in range(start, len(values)):
        sub = values[:end + 1]
        w_half = _wma(sub, half)
        w_full = _wma(sub, length)
        raw.append(2 * w_half - w_full)
    return _wma(raw, root)

def analyze(sym,access):
    cs=candles(sym,access)
    if len(cs)<25: raise RuntimeError(f"{sym}: insufficient daily candles")
    recent=cs[-40:]
    vols=[float(x.get("volume") or 0) for x in recent]

    # Exact OrderFlow_Simple_v1 math supplied by the user:
    # buy_ratio=(close-low)/(high-low), sell_ratio=(high-close)/(high-low)
    # net_delta=buy_vol-sell_vol, smoothDelta=EMA(net_delta,5)
    # deltaSlope=smoothDelta-smoothDelta[3]
    deltas=[]; buy_vols=[]; sell_vols=[]
    for bar in recent:
        hi=float(bar.get("high") or 0); lo=float(bar.get("low") or 0)
        close=float(bar.get("close") or 0); vol=float(bar.get("volume") or 0)
        rng=hi-lo
        buy_ratio=0.5 if rng==0 else (close-lo)/rng
        sell_ratio=0.5 if rng==0 else (hi-close)/rng
        bv=vol*buy_ratio; sv=vol*sell_ratio
        buy_vols.append(bv); sell_vols.append(sv); deltas.append(bv-sv)

    smooths=_ema(deltas,5)
    smooth=smooths[-1]
    slope=smooth-smooths[-4]  # ThinkScript smoothDelta - smoothDelta[3]
    prev_slope=smooths[-2]-smooths[-5]
    strong_buy=smooth>0 and slope>=0
    weak_buy=smooth>0 and slope<0
    strong_sell=smooth<0 and slope<=0
    weak_sell=smooth<0 and slope>0

    if strong_buy: state="GREEN"
    elif weak_buy: state="YELLOW"
    elif strong_sell: state="RED"
    else: state="SKY"

    tot=buy_vols[-1]+sell_vols[-1]
    buy_pct=50 if tot==0 else round(buy_vols[-1]/tot*100)
    avg20=sum(vols[-21:-1])/20 if sum(vols[-21:-1]) else 1
    vr=vols[-1]/avg20
    o=float(recent[-1].get("open") or 0); close=float(recent[-1].get("close") or 0)
    ch=((close/o)-1)*100 if o else 0
    cross_up=smooth>0 and smooths[-2]<=0
    cross_down=smooth<0 and smooths[-2]>=0

    closes=[float(x.get("close") or 0) for x in recent]
    hull21=_hma_at(closes,21)
    prev_hull21=_hma_at(closes[:-1],21)
    hull21_reclaim=bool(
        hull21 is not None and prev_hull21 is not None and
        closes[-1] >= hull21 and closes[-2] < prev_hull21
    )

    # Test signals:
    # A = sell pressure slowdown begins: Smooth slope flips from <=0 to >0 while Smooth remains below zero.
    # B = A-style improving Smooth plus a fresh HULL21 reclaim.
    signal_a=bool(smooth < 0 and slope > 0 and prev_slope <= 0)
    signal_b=bool(slope > 0 and hull21_reclaim)

    return {"ticker":sym,"state":state,"delta_up":slope>0,
            "smooth_delta":round(smooth,0),"delta_slope":round(slope,0),
            "buy_pct":buy_pct,"buy_volume":round(buy_vols[-1]),"sell_volume":round(sell_vols[-1]),
            "zero_cross_up":cross_up,"zero_cross_down":cross_down,
            "signal_a":signal_a,"signal_b":signal_b,
            "hull21":round(hull21,2) if hull21 is not None else None,
            "hull21_reclaim":hull21_reclaim,
            "volume":int(vols[-1]),"volume_ratio":round(vr,2),"price":round(close,2),
            "day_change_pct":round(ch,2),
            "status":("매수 추세" if strong_buy else "매수세 둔화" if weak_buy else "매도 추세" if strong_sell else "매도세 둔화(반등임박)")}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q=parse_qs(urlparse(self.path).query); only=(q.get("ticker",[""])[0] or "").upper().strip()
        try:
            access=token(); sectors={}; flat=[]
            groups={"Custom":[only]} if only else UNIVERSE
            for sector,syms in groups.items():
                rows=[]
                for sym in syms:
                    try: rows.append(analyze(sym,access))
                    except Exception as e: rows.append({"ticker":sym,"error":str(e)})
                sectors[sector]=rows; flat += [x for x in rows if not x.get("error")]
            turns=sorted([x for x in flat if x["state"] == "SKY" and x["delta_up"]],
                         key=lambda x:-x["delta_slope"])[:12]
            out(self,200,{"timeframe":"DAILY","source":"Schwab Trader API",
                "delta_method":"OrderFlow_Simple_v1: estimated buy/sell volume from daily candle location; EMA(5), slope(3)",
                "sectors":sectors,"delta_turn":turns})
        except Exception as e: out(self,500,{"error":str(e),"source":"Schwab Trader API"})
