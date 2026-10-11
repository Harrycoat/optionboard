#!/usr/bin/env python3
"""Build a versioned Leader DB universe snapshot from the existing industry page.
This is a catalogue snapshot, NOT a nightly market scan or a verified grade.
No market-data calls, trading, extra secrets, or network access.
"""
import argparse
import datetime as dt
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "public" / "industry-leaders.html"
OUT = ROOT / "data" / "leaders" / "snapshots"
SECTOR = re.compile(r"\{id:'(?P<id>[^']+)',name:'(?P<name>[^']+)',en:'[^']+',etf:'(?P<etf>[^']+)',desc:'[^']*',stocks:\[(?P<stocks>.*?)\]\}", re.S)
PAIR = re.compile(r"\['(?P<symbol>[A-Z.]+)','(?P<company>[^']+)'\]")
OWNER_EXAMPLES = {"NVDA", "PLTR", "TSLA"}

def extract(source: str):
    results = []
    seen = set()
    for sector in SECTOR.finditer(source):
        stocks = PAIR.findall(sector.group("stocks"))
        if not stocks:
            raise ValueError("EMPTY_SECTOR:" + sector.group("id"))
        for symbol, company in stocks:
            if symbol in seen:
                raise ValueError("DUPLICATE_TICKER:" + symbol)
            seen.add(symbol)
            results.append({
                "ticker": symbol, "company": company,
                "industry_id": sector.group("id"), "industry_name": sector.group("name"),
                "industry_etf": sector.group("etf"),
                "grade": None, "grade_status": "PENDING_VERIFICATION",
                "proposed_grade": "A+" if symbol in OWNER_EXAMPLES else None,
                "evidence": [], "evaluated_at": None,
            })
    if len(results) < 40:
        raise ValueError("INCOMPLETE_LEADER_SOURCE: less than 40")
    return sorted(results, key=lambda x: (x["industry_id"], x["ticker"]))

def build(day: str):
    dt.date.fromisoformat(day)
    universe = extract(SOURCE.read_text(encoding="utf-8"))
    return {
        "schema_version": 1, "snapshot_date": day, "type": "LEADER_UNIVERSE_ONLY",
        "source": "public/industry-leaders.html", "market_data_status": "NOT_SCANNED",
        "grade_evaluation_status": "NOT_EVALUATED",
        "universe_count": len(universe), "leaders": universe,
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    p.add_argument("--write", action="store_true", help="Write a dated JSON file, without overwriting")
    args = p.parse_args()
    obj = build(args.date)
    if args.write:
        OUT.mkdir(parents=True, exist_ok=True)
        dest = OUT / (args.date + ".json")
        with dest.open("x", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(str(dest))
    else:
        print(json.dumps({"snapshot_date": obj["snapshot_date"], "universe_count": obj["universe_count"], "market_data_status": obj["market_data_status"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
