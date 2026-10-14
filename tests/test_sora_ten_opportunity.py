"""Tests for the SORA 10 opportunity engine. No broker or market requests."""
import unittest,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","api"))
from sora_ten_opportunity_engine import STRATEGIES,daily_features,decide,risk_size,rank_watch

def bars():
 out=[]
 for i in range(210):
  c=100+i*.12
  out.append({"t":(i+1)*86400000,"o":c-.3,"h":c+.8,"l":c-.8,"c":c,"v":1000,"complete":True})
 return out

class TenRobots(unittest.TestCase):
 def test_ten_strategies(self):self.assertEqual(len(STRATEGIES),10)
 def test_completed_bars_only(self):
  a=bars();a[-1]["complete"]=False
  with self.assertRaises(ValueError):daily_features(a)
 def test_near_watch_not_fill(self):
  f=daily_features(bars());f["near50"]=True
  r=decide("SORA-04","NVDA",f,verified_member=True)
  self.assertEqual(r["status"],"WATCH");self.assertFalse(r["paper_trade_executed"])
 def test_tos_proxy_not_entry(self):
  f=daily_features(bars());f["near50"]=True
  ctx={"completed_30m":True,"selling_easing":True,"reference_high_break":True,"tos_smooth_verified":False}
  r=decide("SORA-04","NVDA",f,verified_member=True,context=ctx)
  self.assertEqual(r["status"],"READY")
 def test_unverified_membership(self):
  self.assertEqual(decide("SORA-04","NVDA",daily_features(bars()),verified_member=False)["status"],"WAIT")
 def test_dca_waits_for_safety(self):
  r=decide("SORA-09","QLD",None,context={"qqq_daily_verified":True,"qqq_sma50_touch":True})
  self.assertEqual(r["status"],"READY")
 def test_sizing_limited_by_cash_and_risk(self):
  self.assertEqual(risk_size(100000,100,95,equity=100000,risk_fraction=.005,max_position_fraction=.10),100)
 def test_rank_watch(self):
  entries=[{"status":"WAIT","account_id":"SORA-01","ticker":"A"},{"status":"WATCH","account_id":"SORA-02","ticker":"B"}]
  self.assertEqual(rank_watch(entries)[0]["ticker"],"B")
if __name__=="__main__":unittest.main()
