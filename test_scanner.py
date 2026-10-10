import unittest
from decimal import Decimal
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scanner import (D, Level, assess, collect_demo, depth_cost, evaluate,
                     get_asset_ids, parse_book, fee_rate, market_ready)

class TestScanner(unittest.TestCase):
    def test_market_v2(self):
        m = collect_demo()[0]['market']
        self.assertEqual(get_asset_ids(m), ('10','11'))
        self.assertTrue(market_ready(m))
        self.assertEqual(fee_rate(m)[0], D('0.04'))

    def test_v1(self):
        m = {'version':'v1','clobTokenIds':'["321","322"]','outcomes':'["Yes","No"]'}
        self.assertEqual(get_asset_ids(m), ('321','322'))
        self.assertIsNone(get_asset_ids({**m,'outcomes':'["A","B"]'}))

    def test_negative_risk_skip_restriction(self):
        m = collect_demo()[0]['market']
        self.assertFalse(market_ready({**m, 'restricted':True}))
        self.assertFalse(market_ready({**m, 'closed':True}))

    def test_book_sorted_best_ask(self):
        lv, ms, _, _ = parse_book({'asks':[{'price':'.93','size':'1'}, {'price':'.42','size':'2'}], 'min_order_size':'5'})
        self.assertEqual(lv[0].price,D('.42'))
        self.assertEqual(ms,D('5'))

    def test_fee_math(self):
        r=depth_cost([Level(D('.5'), D('100'))],D('100'),D('.07'))
        self.assertEqual(r[0], D('50'))
        self.assertEqual(r[1], D('1.75'))

    def test_budget_10_infeasible_with_min_usdc(self):
        row=collect_demo()[0]
        _, res=assess(row,[D(10), D(50)], D('.002'),60,10)
        self.assertEqual(res[0]['status'],'INFEASIBLE')
        self.assertEqual(res[1]['status'],'PAPER_POSITIVE')
        self.assertGreater(res[1]['pnl'],0)

    def test_no_fake_profit_when_prices_sum_one(self):
        row=collect_demo()[1]
        _, results=assess(row,[D(50)],D('.002'),60,10)
        self.assertEqual(results[0]['status'],'PAPER_NEGATIVE')

    def test_depth_stops_fake_opportunity(self):
        row=collect_demo()[2]
        _, results=assess(row,[D(50)],D('.002'),60,10)
        self.assertNotEqual(results[0]['status'],'PAPER_POSITIVE')

    def test_timestamps_prevent_stale_books(self):
        row=collect_demo()[0]
        row['yes']['timestamp']='1000000000000'
        state, results=assess(row,[D(50)],D('.002'),60,10)
        self.assertEqual(results,[])
        self.assertIn('stale',state['reason'])

    def test_fee_exponent_unsupported(self):
        row=collect_demo()[0]['market']
        row['feeSchedule']['exponent']=2
        self.assertIsNone(fee_rate(row))

if __name__ == '__main__': unittest.main()
