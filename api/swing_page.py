from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import sys
sys.path.insert(0,str(Path(__file__).parent))
from swing_access import require_access
PAGES={'paper':'intc-paper-lab.html','top100':'swing-top100.html','chart':'swing-radar-chart.html','sector':'swing-radar.html'}
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not require_access(self):return
        page=parse_qs(urlparse(self.path).query).get('page',['top100'])[0]
        if page not in PAGES:self.send_error(404);return
        data=(Path(__file__).parent.parent/'public'/PAGES[page]).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"frame-ancestors 'self'")
        self.end_headers();self.wfile.write(data)
