"""Local tests: python -m unittest discover -s tests -p 'test_sora_paper_gate.py'"""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from sora_entry_gate import assess_entry
from sora_paper_engine import execute_paper_buy

D={"sma50_rising":True,"near_sma20":True,"near_sma50":False,"sell_pressure_easing":True}
M={"completed":True,"smooth_turn_up":True,"reference_high_break":True,"smooth_source":"VERIFIED_TOS"}
class EntryTests(unittest.TestCase):
 def test_entry(self):
  self.assertEqual(assess_entry(D,M,constituent_verified=True)["status"],"ENTRY")
 def test_unverified_index(self):
  self.assertEqual(assess_entry(D,M,constituent_verified=False)["status"],"WAIT")
 def test_wait_for_smooth(self):
  self.assertEqual(assess_entry(D,dict(M,smooth_turn_up=False),constituent_verified=True)["status"],"WATCH")
 def test_no_incomplete_candle(self):
  self.assertEqual(assess_entry(D,dict(M,completed=False),constituent_verified=True)["status"],"WAIT")
 def test_proxy_only_cannot_fill(self):
  self.assertEqual(assess_entry(D,dict(M,smooth_source="EVP_ESTIMATE"),constituent_verified=True)["status"],"WATCH")
 def test_paper_cash(self):
  ledger={"rules":{"maxPositions":12},"accounts":[{"id":"SORA-01","cash":100000,"initialCash":100000,"positions":[],"trades":[]}]}
  order={"account_id":"SORA-01","ticker":"NVDA","classification":"ENTRY","signal_source":"swing_signals.classify","data_status":"VERIFIED_COMPLETED_BARS","daily_sma50_rising":True,"intraday_30m_ready":True,"fill_price":200,"quantity":10,"fill_time_utc":"2026-10-14T14:31:00Z","signal_bar_end_utc":"2026-10-14T14:30:00Z","data_provenance":"unit-test synthetic, not trade","reason":"test"}
  out,receipt=execute_paper_buy(ledger,order,allowed_symbols={"NVDA"})
  self.assertEqual(out["accounts"][0]["cash"],98000)
  self.assertEqual(receipt["status"],"PENDING_DURABLE_COMMIT")
  self.assertEqual(ledger["accounts"][0]["positions"],[])
if __name__=="__main__":unittest.main()
