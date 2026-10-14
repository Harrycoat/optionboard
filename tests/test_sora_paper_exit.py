import unittest,sys,os
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","api"))
from sora_paper_exit import plan_exit,apply_paper_exit

def bars(last=90):
 a=[]
 for i in range(52):
  c=100+i*.1
  a.append({"c":c,"complete":True})
 a[-1]["c"]=last
 return a

class PaperExits(unittest.TestCase):
 def test_half_and_full(self):
  p=[{"ticker":"NVDA","quantity":101}]
  a=plan_exit(p,bars(110))
  self.assertEqual(a["decisions"],[])
  result=plan_exit(p,bars(90))
  self.assertEqual(result["decisions"][0]["exit_stage"],"SMA50_FULL")
 def test_half_then_full(self):
  data=bars(105)
  # Explicitly construct a close below 20MA, but above the 50MA.
  for i in range(52):
   data[i]["c"]=100 if i<32 else 120
  data[-1]["c"]=110
  h=plan_exit([{"ticker":"NVDA","quantity":101}],data)["decisions"]
  self.assertEqual(h[0]["quantity"],51)
  self.assertEqual(h[0]["exit_stage"],"SMA20_HALF")
  data[-1]["c"]=90
  result=plan_exit([{"ticker":"NVDA","quantity":50}],data,[{"ticker":"NVDA","exit_stage":"SMA20_HALF"}])
  self.assertEqual(result["decisions"][0]["quantity"],50)
  self.assertEqual(result["decisions"][0]["exit_stage"],"SMA50_FULL")
 def test_no_duplicate_half(self):
  data=bars(90)
  sales=[{"ticker":"NVDA","exit_stage":"SMA20_HALF"}]
  self.assertFalse(any(x["exit_stage"]=="SMA20_HALF" for x in plan_exit([{"ticker":"NVDA","quantity":50}],data,sales)["decisions"]))
 def test_incomplete(self):
  b=bars();b[-1]["complete"]=False
  with self.assertRaises(ValueError):plan_exit([{"ticker":"NVDA","quantity":100}],b)
 def test_apply_pending_only(self):
  state={"accounts":[{"id":"SORA-02","cash":1000,"positions":[{"ticker":"NVDA","quantity":101}],"trades":[]}]}
  proposal={"ticker":"NVDA","quantity":51,"exit_stage":"SMA20_HALF","reason":"daily close"}
  new,receipt=apply_paper_exit(state,"SORA-02",proposal,fill_price=100,fill_time_utc="2026-10-14T21:00:00Z",signal_bar_end_utc="2026-10-14T20:00:00Z",provenance="verified",request_id="exit-001")
  self.assertEqual(new["accounts"][0]["positions"][0]["quantity"],50)
  self.assertEqual(new["accounts"][0]["cash"],6100)
  self.assertEqual(receipt["status"],"PENDING_DURABLE_COMMIT")
  self.assertEqual(state["accounts"][0]["cash"],1000)
if __name__=="__main__":unittest.main()
