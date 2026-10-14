"""Read-only SORA paper-entry readiness, no orders or recorded fills."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import os,sys,json
sys.path.insert(0,os.path.dirname(__file__))
from swing_access import require_access
from swing_radar_chart import aggregates,indicators
from swing_signals import daily_setup,intraday_setup,classify

class handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if not require_access(self):return
  ticker=(parse_qs(urlparse(self.path).query).get("ticker") or ["NVDA"])[0].upper()
  if ticker!="NVDA":
   self._reply(400,{"error":"Pilot supports NVDA only"});return
  try:
   d=aggregates(ticker,"1d")
   m=aggregates(ticker,"30m")
   if len(d)<55 or len(m)<35:raise ValueError("INSUFFICIENT_COMPLETED_BARS")
   daily=daily_setup(d);intra=intraday_setup(m)
   classification=classify(daily,intra)
   et=ZoneInfo("America/New_York")
   date_d=datetime.fromtimestamp(d[-1]["t"]/1000,timezone.utc).astimezone(et).date().isoformat()
   timestamp=datetime.fromtimestamp(intra["bar_end_ms"]/1000,timezone.utc).isoformat()
   # The radar computes Hull20 and EVP, NOT actual TOS Hull21/Smooth.
   self._reply(200,{"ticker":ticker,"status":"WATCH" if classification=="ENTRY" else classification,
    "paper_buy":"NOT_EXECUTED","reason":"Actual TOS Smooth/Hull21 and durable transaction storage are not connected; no fill is recorded.",
    "daily_date_et":date_d,"last_30m_bar_end_utc":timestamp,
    "daily":{"sma50_rising":daily["sma50_rising"],"touched_sma20":daily["touched_sma20"],
             "daily_close":daily["close"],"sma20":daily["sma20"],"sma50":daily["sma50"],
             "distance_sma50_pct":round((daily["close"]/daily["sma50"]-1)*100,2),
             "sell_proxy_easing":daily["pressure_easing"]},
    "intraday":{"hull20_proxy":intra["hull20"],"evp_proxy_rising":intra["evp_rising"],
                "evp_proxy_turn_up":intra["evp_turn_up"],"hull20_cross":intra["cross_now"],
                "classification":classification},
    "source":"Massive delayed OHLCV; simulated proxy indicators, not TOS Smooth"})
  except Exception as ex:
   self._reply(503,{"error":"DATA_UNAVAILABLE","reason":str(ex).split(":")[0][:80],"paper_buy":"NOT_EXECUTED"})
 def _reply(self,code,obj):
  data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
  self.send_response(code)
  self.send_header("Content-Type","application/json; charset=utf-8")
  self.send_header("Cache-Control","no-store")
  self.send_header("Content-Length",str(len(data)))
  self.end_headers();self.wfile.write(data)
