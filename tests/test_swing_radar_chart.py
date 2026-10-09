"""Minimal deterministic smoke tests. Run from repository root: python -m unittest tests.test_swing_radar_chart"""
import unittest
from api.swing_radar_chart import hull20, indicators, VALID_TF

class ChartCalculationTests(unittest.TestCase):
    def test_hull20_warmup_and_flat_line(self):
        result=hull20([100.0]*30)
        self.assertTrue(all(x is None for x in result[:22]))
        self.assertTrue(all(abs(x-100.0)<1e-8 for x in result[23:]))

    def test_hull20_tracks_increasing_prices(self):
        result=hull20([float(i) for i in range(1,61)])
        self.assertGreater(result[-1],result[-2])

    def test_evp_zero_on_midpoint_closes(self):
        rows=[{'t':i*300000,'o':100,'h':102,'l':98,'c':100,'v':1000} for i in range(40)]
        out=indicators(rows)
        self.assertEqual(out[-1]['evp'],0)
        self.assertIsNotNone(out[-1]['hull20'])

    def test_all_expected_timeframes(self):
        self.assertEqual(set(VALID_TF), {'1d','4h','1h','30m','10m','5m'})

if __name__=='__main__':
    unittest.main()
