# Vercel serverless: GET /api/filings?ticker=TSLA
# SEC EDGAR 최근 공시 (키 불필요). 8-K/10-Q/10-K 등 주요 서식 위주 상위 5건.
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import requests

UA = {"User-Agent": "gexoption/1.0 (tmartbakersfield@gmail.com)", "Accept": "application/json"}

FORM_LABELS = {
    "8-K": "주요 공시", "8-K/A": "주요 공시(정정)",
    "10-Q": "분기보고서", "10-Q/A": "분기보고서(정정)",
    "10-K": "연간보고서", "10-K/A": "연간보고서(정정)",
    "S-1": "증권신고서", "S-3": "증권신고서",
    "424B4": "공모 확정서",
    "6-K": "외국기업 공시",
    "DEF 14A": "주주총회 위임장",
    "4": "내부자 거래", "4/A": "내부자 거래(정정)",
    "3": "내부자 보유", "5": "내부자 연간보고",
    "13D": "5% 이상 보유", "13G": "5% 이상 보유(간이)",
    "144": "대주주 매각 예정",
}

WANT = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "S-1", "S-3",
        "424B4", "6-K", "DEF 14A", "4", "13D", "13G", "144"}

_ticker_map = None

def _load_ticker_map():
    global _ticker_map
    if _ticker_map is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                          headers=UA, timeout=10)
        r.raise_for_status()
        data = r.json()
        _ticker_map = {v["ticker"].upper(): str(v["cik_str"]) for v in data.values()}
    return _ticker_map

def _recent_filings(cik):
    cik10 = cik.zfill(10)
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik10}.json",
                     headers=UA, timeout=10)
    r.raise_for_status()
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    out = []
    for i, form in enumerate(forms):
        if form not in WANT:
            continue
        acc = accs[i].replace("-", "")
        doc = docs[i]
        out.append({
            "form": form,
            "label": FORM_LABELS.get(form, form),
            "date": dates[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}",
        })
        if len(out) >= 5:
            break
    return out

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            ticker = (query.get("ticker", [""])[0] or "").strip().upper()
            if not ticker:
                raise ValueError("ticker 파라미터가 필요합니다. 예: /api/filings?ticker=TSLA")
            cmap = _load_ticker_map()
            cik = cmap.get(ticker)
            if not cik:
                payload = {"ticker": ticker, "filings": [], "filings_status": "unknown_ticker"}
            else:
                payload = {"ticker": ticker, "filings": _recent_filings(cik),
                           "filings_status": "ok"}
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
        except Exception as e:
            body = json.dumps({"error": str(e), "filings_status": "error"},
                              ensure_ascii=False).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "s-maxage=1800")
        self.end_headers()
        self.wfile.write(body)
