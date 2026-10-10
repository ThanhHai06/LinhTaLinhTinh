import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scanner import collect_live, collect_demo, assess, D

class APIAdapterContractTests(unittest.TestCase):
    def test_official_gamma_then_clob_batch_contract(self):
        row=collect_demo()[0]
        m=row['market']
        with patch('scanner.get_json',side_effect=[
            {'markets':[m],'next_cursor':None},
            [row['no'],row['yes']]
        ]) as mock_get:
            result=collect_live(1,50)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['yes']['asset_id'],'10')
            self.assertEqual(result[0]['no']['asset_id'],'11')
            self.assertIn('/markets/keyset?',mock_get.call_args_list[0].args[0])
            self.assertTrue(mock_get.call_args_list[1].args[0].endswith('/books'))
            self.assertEqual(mock_get.call_args_list[1].kwargs['payload'],
                [{'token_id':'10'},{'token_id':'11'}])

    def test_live_book_market_identity_check(self):
        row=collect_demo()[0]
        row['market']['conditionId']='0xabc'
        row['yes']['market']='0xabc'
        row['no']['market']='0xdef'
        state,res=assess(row,[D(50)],D('.002'),60,10)
        self.assertEqual(res,[])
        self.assertIn('book_market_mismatch',state['reason'])
