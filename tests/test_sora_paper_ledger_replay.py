import unittest,os,sys
sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","api"))
from sora_paper_ledger_replay import replay,confirm_balance
def trade(side,ticker,quantity,fill_price,key):
 return dict(side=side,ticker=ticker,quantity=quantity,fill_price=fill_price,request_id=key,mode="PAPER")
class LedgerReplay(unittest.TestCase):
 def test_nvda_tsla_existing(self):
  ledger={"initial_cash":100000,"cash":41400,"trades":[trade("BUY","NVDA",100,221,"a"),trade("BUY","TSLA",100,365,"b")]}
  x=confirm_balance(ledger)
  self.assertEqual(x["cash"],41400)
  self.assertEqual(len(x["positions"]),2)
 def test_partial_then_final_exit(self):
  trades=[trade("BUY","NVDA",100,100,"a"),trade("SELL","NVDA",50,110,"b"),trade("SELL","NVDA",50,90,"c")]
  x=replay({"initial_cash":100000,"trades":trades})
  self.assertEqual(x["cash"],100000)
  self.assertEqual(x["positions"],[])
  self.assertEqual(x["realized_pnl"],0)
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):replay({"initial_cash":100000,"trades":[trade("BUY","NVDA",1,100,"a"),trade("BUY","NVDA",1,100,"a")]})
 def test_oversell_rejected(self):
  with self.assertRaises(ValueError):replay({"initial_cash":100000,"trades":[trade("BUY","NVDA",2,100,"a"),trade("SELL","NVDA",3,100,"b")]})
 def test_forged_cash_rejected(self):
  with self.assertRaises(ValueError):confirm_balance({"initial_cash":100000,"cash":99999,"trades":[]})
if __name__=="__main__":unittest.main()
