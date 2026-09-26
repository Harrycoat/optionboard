import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.gexoption.com"
OUT = Path("public/swing-virtual-trades.json")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "GEXOption-Swing-Test/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def load_existing():
    if not OUT.exists():
        return {"updated_at": None, "trades": []}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": None, "trades": []}

def pnl_pct(entry, last):
    if not entry:
        return 0.0
    return round((last / entry - 1.0) * 100.0, 2)

def main():
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    data = load_existing()
    trades = data.get("trades") or []

    rows = []
    for i in range(12):
        payload = fetch_json(f"{BASE}/api/search?mode=swing_volume_heatmap&sector={i}")
        sector = payload.get("sector") or f"Sector {i+1}"
        for row in payload.get("rows") or []:
            if row.get("error"):
                continue
            item = dict(row)
            item["sector"] = sector
            rows.append(item)

    # Latest close by ticker.
    latest = {}
    for row in rows:
        latest[row["ticker"]] = row

    # Mark-to-market every existing virtual trade.
    for t in trades:
        row = latest.get(t.get("ticker"))
        if not row:
            continue
        last = float(row.get("price") or t.get("last") or t.get("entry") or 0)
        t["last"] = last
        t["last_date"] = day
        t["pnl_pct"] = pnl_pct(float(t.get("entry") or 0), last)
        t["max_pnl_pct"] = max(float(t.get("max_pnl_pct") or t["pnl_pct"]), t["pnl_pct"])
        t["min_pnl_pct"] = min(float(t.get("min_pnl_pct") or t["pnl_pct"]), t["pnl_pct"])

    existing = {t.get("key") for t in trades}
    seen_today = set()

    # Record new A/B signals once per ticker/type/day.
    for row in rows:
        ticker = row.get("ticker")
        sector = row.get("sector")
        for typ, flag in (("A", row.get("signal_a")), ("B", row.get("signal_b"))):
            if not flag:
                continue
            key = f"{day}|{ticker}|{typ}"
            dedupe = (ticker, typ)
            if key in existing or dedupe in seen_today:
                continue
            seen_today.add(dedupe)
            entry = float(row.get("price") or 0)
            trades.insert(0, {
                "key": key,
                "date": day,
                "ticker": ticker,
                "sector": sector,
                "type": typ,
                "entry": entry,
                "last": entry,
                "last_date": day,
                "pnl_pct": 0.0,
                "max_pnl_pct": 0.0,
                "min_pnl_pct": 0.0,
                "smooth": row.get("smooth_delta"),
                "hull21": row.get("hull21"),
            })

    data = {
        "updated_at": now.isoformat(),
        "method": {
            "A": "SKY / Smooth slope turns from <=0 to >0 while Smooth remains below zero",
            "B": "Smooth slope >0 plus fresh HULL21 reclaim",
        },
        "trades": trades[:1000],
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(data['trades'])} virtual trades")

if __name__ == "__main__":
    main()
