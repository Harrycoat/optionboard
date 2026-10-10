"""Single Vercel Python entrypoint; route to existing API handlers."""
from http.server import BaseHTTPRequestHandler
from importlib import import_module
from urllib.parse import parse_qsl, urlencode, urlsplit

_ALLOWED = {
    "search", "bars", "candles", "call_wall_monitor", "schwab_quote",
    "earnings_scan", "config", "schwabquote", "filings",
    "swing_volume_heatmap", "swing_page", "sora_page", "sora_chat",
    "swing_radar_chart", "swing_radar", "oi_change",
}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._dispatch("do_GET")

    def do_POST(self):
        self._dispatch("do_POST")

    def _dispatch(self, method):
        parsed = urlsplit(self.path)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        explicit = [v for k, v in pairs if k == "__route"]
        name = explicit[-1] if explicit else parsed.path.removeprefix("/api/")
        if name not in _ALLOWED:
            self.send_error(404, "Unknown API route")
            return
        try:
            target = import_module("api." + name).handler
        except Exception:
            self.send_error(500, "API handler unavailable")
            return
        if not issubclass(target, BaseHTTPRequestHandler):
            self.send_error(500, "Invalid API handler")
            return
        action = getattr(target, method, None)
        if action is None:
            self.send_error(405, "Method not allowed")
            return
        params = [(k, v) for k, v in pairs if k != "__route"]
        self.path = "/api/" + name + ("?" + urlencode(params) if params else "")
        self.__class__ = target
        action(self)
