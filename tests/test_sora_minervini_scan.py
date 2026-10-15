import unittest,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","api"))
from sora_minervini_scan import analyze
def candles(n=220):
 result=[]
 for i in range(n):
  p=100+i*.22
  result.append({"t":(i+1)*86400000,"o":p-.2,"h":p+.8,"l":p-.9,"c":p,"v":1000,"complete":True})
 return result
class MinerviniScanner(unittest.TestCase):
 def test_valid_completed_data(self):
  r=analyze(candles())
  self.assertIn("pivot",r)
  self.assertFalse(r["buy_signal"])
  self.assertEqual(len(r["contractions_pct"]),3)
 def test_reject_incomplete(self):
  d=candles();d[-1]["complete"]=False
  with self.assertRaises(ValueError):analyze(d)
 def test_reject_short(self):
  with self.assertRaises(ValueError):analyze(candles(40))
if __name__=="__main__":unittest.main()
