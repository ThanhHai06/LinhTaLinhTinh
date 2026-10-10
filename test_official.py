import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scanner import collect_demo,get_asset_ids,fee_rate,assess,D
from realtime import normalize_event, apply_book_event, books_index, from_catalog
from rewards import reward_rows
from store import write_scan, summary, connect, write_event

class OfficialSourceTests(unittest.TestCase):
    def test_missing_protocol_version_fails_closed(self):
        m={'clobTokenIds':'["12","13"]','outcomes':'["Yes","No"]'}
        self.assertIsNone(get_asset_ids(m))

    def test_v2_position_ids_win_even_with_ctf_field(self):
        m={'version':'v2','clobTokenIds':'["12","13"]','positionIds':['22','23'],
           'outcomes':'["Yes","No"]'}
        self.assertEqual(get_asset_ids(m),('22','23'))

    def test_unknown_fee_must_not_be_assumed_zero(self):
        self.assertIsNone(fee_rate({'feesEnabled':True}))
        self.assertEqual(fee_rate({'feesEnabled':False})[0],D('0'))
        self.assertIsNone(fee_rate({'feesEnabled':True,'feeSchedule':{'rate':'.04','exponent':2}}))

    def test_ws_payload_and_delta_not_assumed_full_book(self):
        b=books_index(collect_demo())
        b0=b['10']
        updates=normalize_event(json.dumps([
           {'event_type':'price_change','price_changes':[{'asset_id':'10','side':'BUY','price':'.5','size':'20'}]},
           {'event_type':'book','asset_id':'10','asks':[{'price':'.45','size':'20'}],
             'timestamp':b0['timestamp']}]))
        self.assertFalse(apply_book_event(b,updates[0]))
        self.assertEqual(b['10'],b0)
        self.assertTrue(apply_book_event(b,updates[1]))
        self.assertEqual(b['10']['asks'][0]['price'],'.45')
        self.assertEqual(b['10']['min_order_size'],'5')

    def test_reward_pool_is_not_my_profit(self):
        m=collect_demo()[0]['market']
        m.update({'clobRewards':[{'id':'reward','rewardsDailyRate':'100',
                     'startDate':'2026-10-01','endDate':'2026-10-30'}],
                  'rewardsMinSize':'100','rewardsMaxSpread':'3'})
        rows=reward_rows([m],date(2026,10,10))
        self.assertEqual(rows[0]['pool_daily_usdc'],'100')
        self.assertIn('UNKNOWN',rows[0]['note'])
        self.assertEqual(reward_rows([m],date(2026,11,1)),[])

    def test_db_journal(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'tests.sqlite3'
            doc={'generated_utc':'2026-10-10T00:00:00+00:00','mode':'demo',
                 'markets_accepted':1,'markets_with_both_books':1,'data_type':'SYNTHETIC',
                 'results':[{'market_id':'1','question':'Fake','capital':D(50),
                             'status':'PAPER_POSITIVE','pnl':D('.25')}]}
            self.assertEqual(write_scan(f,doc),1)
            with connect(f) as db:
                write_event(db,{'event_type':'book','asset_id':'10','market':'0xabc'})
            r=summary(f)
            self.assertEqual(r['scans'],1)
            self.assertEqual(r['paper_positive_rows'],1)
            self.assertEqual(r['ws_events'],1)
