"""Focused tests for SORA 30 paper robots. Run: python -m unittest tests.test_sora_robots"""
import unittest,sys,os,json
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","api"))
from sora_robots import evaluate,evaluate_all,setup_for

def acct(n):return {"id":"SORA-%02d"%n}
def signal():
 return dict(ticker="NVDA",completed_daily=True,completed_30m=True,sma50_rising=True,
 touch_sma20=True,sell_pressure_easing=True,reference_high_break=True,
 smooth_up=True,smooth_source="VERIFIED_TOS",bar_provenance="verified_tos",
 signal_time_utc="2026-10-14T18:30:00Z")
class Robots(unittest.TestCase):
 def test_all_30_distinct(self):
  config={"accounts":[acct(i) for i in range(1,31)]}
  out=evaluate_all(config)
  self.assertEqual(len(out["robots"]),30)
  self.assertEqual(out["paper_trades_executed"],0)
  self.assertTrue(all(r["status"]=="WAIT" for r in out["robots"]))
 def test_no_proxy_smooth_entry(self):
  c=signal();c["smooth_source"]="EVP_PROXY"
  self.assertEqual(evaluate(acct(1),c,verified_members={"NVDA"})["status"],"WATCH")
 def test_core_verified_candidate_is_not_executed(self):
  x=evaluate(acct(1),signal(),verified_members={"NVDA"})
  self.assertEqual(x["status"],"ENTRY_ELIGIBLE")
  self.assertFalse(x["paper_trade_executed"])
 def test_membership_fail_closed(self):
  self.assertEqual(evaluate(acct(1),signal(),verified_members=None)["status"],"WAIT")
 def test_dca_is_never_auto_eligible(self):
  self.assertEqual(evaluate(acct(29),dict(ticker="QLD",qqq_daily_verified=True,sma50_touch=True))["status"],"WATCH")
  self.assertEqual(evaluate(acct(30),dict(ticker="TQQQ",qqq_daily_verified=True,sma200_touch=True))["status"],"WATCH")
 def test_missing_strategy_rule(self):
  self.assertEqual(evaluate(acct(20),signal(),verified_members={"NVDA"})["status"],"WAIT")
if __name__=="__main__":unittest.main()
