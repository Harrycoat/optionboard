import os,sys,unittest,hashlib,base64
from unittest.mock import patch
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','api'))
from swing_access import authorized
from swing_signals import daily_setup,intraday_setup,classify
from swing_radar_chart import resample,session_bounds
class RadarTests(unittest.TestCase):
 def test_auth_fail_closed(self):
  with patch.dict(os.environ,{},clear=True):self.assertEqual(authorized({}),(False,503))
 def test_auth_valid_and_wrong(self):
  with patch.dict(os.environ,{'SWING_RADAR_USER':'owner','SWING_RADAR_PASSWORD_SHA256':hashlib.sha256(b'test-only').hexdigest()}):
   self.assertEqual(authorized({'Authorization':'Basic '+base64.b64encode(b'owner:test-only').decode()}),(True,200))
   self.assertEqual(authorized({}),(False,401))
 def test_sma50_five_session_slope(self):
  rows=[dict(t=i,o=100+i,h=102+i,l=98+i,c=100+i,v=1000) for i in range(80)]
  d=daily_setup(rows);self.assertAlmostEqual(d['sma50'],154.5);self.assertAlmostEqual(d['sma50_slope_pct'],(154.5/149.5-1)*100);self.assertTrue(d['sma50_rising'])
 def test_falling_50ma_cannot_entry(self):
  d={'sma50_rising':False,'pressure_expanding':False};self.assertEqual(classify(d,{'ready':True}),'WEAK')
 def test_data_shortage_raises(self):
  with self.assertRaises(ValueError):daily_setup([])
  with self.assertRaises(ValueError):intraday_setup([])
 def test_completed_and_provisional_30m(self):
  op,cl=session_bounds('2026-10-09');rows=[dict(t=op+i*300000,o=100,h=102,l=98,c=101,v=100) for i in range(12)]
  out=resample(rows,30,op+45*60000);self.assertTrue(out[0]['complete']);self.assertFalse(out[1]['complete']);self.assertEqual(out[0]['v'],600)
 def test_missing_base_bar_flagged(self):
  op,cl=session_bounds('2026-10-09');rows=[dict(t=op+i*300000,o=100,h=102,l=98,c=101,v=100) for i in [0,1,2,4,5]]
  self.assertFalse(resample(rows,30,cl)[0]['coverage_ok'])
 def test_early_close_and_holiday(self):
  op,cl=session_bounds('2026-11-27');self.assertEqual((cl-op)//60000,210)
  rows=[dict(t=op+i*300000,o=100,h=102,l=98,c=101,v=100) for i in range(42)]
  out=resample(rows,240,cl);self.assertEqual(out[0]['end_ms'],cl);self.assertTrue(out[0]['complete']);self.assertTrue(out[0]['coverage_ok'])
  self.assertIsNone(session_bounds('2026-12-25'))
 def test_entry_gated_by_daily_setup(self):
  d=dict(sma50_rising=True,pressure_expanding=False,touched_sma20=True,pressure_easing=True,low_holding=True)
  s=dict(ready=True,hull_distance_pct=.2,watch=True,hull_turned_up=True)
  self.assertEqual(classify(d,s),'ENTRY');d['pressure_easing']=False;self.assertEqual(classify(d,s),'NO_SETUP')
if __name__=='__main__':unittest.main()
