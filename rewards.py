#!/usr/bin/env python3
"""Official Gamma liquidity reward metadata explorer.

NO estimations of your payout: relative scoring uses everybody's resting orders.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date,datetime,timezone
import json
from pathlib import Path
import sys
from typing import Any
from scanner import discover, D, get_asset_ids, market_ready


def optional_list(obj: Any) -> list:
    if isinstance(obj,list):
        return obj
    if isinstance(obj,str):
        try:
            x=json.loads(obj)
            return x if isinstance(x,list) else []
        except ValueError:
            return []
    return []


def reward_rows(markets: list[dict], today:date|None=None) -> list[dict]:
    today=today or datetime.now(timezone.utc).date()
    rows=[]
    for m in markets:
        if not market_ready(m) or not get_asset_ids(m):
            continue
        allocations=optional_list(m.get('clobRewards'))
        for a in allocations:
            if not isinstance(a,dict):
                continue
            start,end=a.get('startDate'),a.get('endDate')
            try:
                if start and date.fromisoformat(str(start)[:10])>today:
                    continue
                if end and date.fromisoformat(str(end)[:10])<today:
                    continue
                daily=D(a.get('rewardsDailyRate',0) or 0)
                if daily<=0:
                    continue
            except (ValueError,TypeError):
                continue
            rows.append({
                'market_id':str(m.get('id','')),
                'question':str(m.get('question','')),
                'slug':str(m.get('slug','')),
                'reward_id':str(a.get('id','')),
                'pool_daily_usdc':str(daily),
                'min_qualifying_size':str(m.get('rewardsMinSize','')),
                'max_qualifying_spread':str(m.get('rewardsMaxSpread','')),
                'start_date':str(start or ''),'end_date':str(end or ''),
                'note':'POOL ONLY. Personal earning UNKNOWN without relative scoring and eligible resting orders.'
            })
    rows.sort(key=lambda r:D(r['pool_daily_usdc']),reverse=True)
    return rows


def main(argv=None)->int:
    p=argparse.ArgumentParser(description='Inspect reward pools; NO maker payout estimate, NO orders')
    p.add_argument('--pages',type=int,default=4)
    p.add_argument('--limit',type=int,default=100)
    p.add_argument('--output',default='reports/reward_pools')
    a=p.parse_args(argv)
    if not 1<=a.pages<=20 or not 1<=a.limit<=500:
        p.error('pages must be 1..20, limit 1..500')
    try:
        markets=discover(a.pages,a.limit)
        rows=reward_rows(markets)
    except RuntimeError as e:
        print('OFFICIAL API ERROR, no synthetic rewards: '+str(e),file=sys.stderr)
        return 2
    dst=Path(a.output)
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.with_suffix('.json').write_text(json.dumps({'source':'Gamma API','observed_utc':datetime.now(timezone.utc).isoformat(),
               'warning':'Pool funding does not establish your earnings, eligibility, or profit. No orders sent.',
               'markets_seen':len(markets),'reward_allocations':rows},indent=2,ensure_ascii=False),encoding='utf-8')
    with dst.with_suffix('.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['market_id','question','slug','reward_id','pool_daily_usdc','min_qualifying_size',
                 'max_qualifying_spread','start_date','end_date','note'])
        w.writeheader();w.writerows(rows)
    print(f"Reward pools: {len(rows)} from {len(markets)} discovered markets. CSV: {dst.with_suffix('.csv')}")
    print('These are POOL ALLOCATIONS, not estimated or earned income.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
