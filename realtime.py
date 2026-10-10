#!/usr/bin/env python3
"""Official public market WebSocket and REST-seeded order book research.

Intentionally does NOT place trades, sign transactions, or calculate fills.
WebSocket book events are observations; REST refreshes are the consistent scan.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import time
from typing import Any

from scanner import D, assess, collect_live, discover, fee_rate, get_asset_ids, market_ready, get_json, CLOB
from store import connect, write_event

URL='wss://ws-subscriptions-clob.polymarket.com/ws/market'


def valid_event(event: Any) -> bool:
    return isinstance(event,dict) and isinstance(event.get('event_type'),str)


def books_index(rows: list[dict]) -> dict[str,dict]:
    index={}
    for item in rows:
        for name in ('yes','no'):
            b=item.get(name)
            if b and b.get('asset_id'):
                index[str(b['asset_id'])]=b
    return index


def normalize_event(raw: Any) -> list[dict]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError,TypeError):
        return []
    if isinstance(payload,list):
        return [x for x in payload if valid_event(x)]
    return [payload] if valid_event(payload) else []


def apply_book_event(index: dict, event: dict) -> bool:
    """Full book WS event replaces local snapshot. Delta events require REST resync.

    A price_change is not treated as a full book; never silently reuse old depth.
    """
    if event.get('event_type')!='book':
        return False
    asset=str(event.get('asset_id',''))
    if asset not in index or not isinstance(event.get('asks'),list):
        return False
    index[asset] = {**index[asset], **event}
    return True


def from_catalog(m: dict, books: dict[str,dict]) -> dict|None:
    ids=get_asset_ids(m)
    if not ids or ids[0] not in books or ids[1] not in books:
        return None
    return {'market':m,'yes':books[ids[0]],'no':books[ids[1]]}


async def run(a) -> int:
    try:
        import websockets
    except ImportError:
        print('Missing websockets. Install: pip install -r requirements.txt',file=sys.stderr)
        return 2
    selected=[]
    for m in discover(a.pages,a.limit):
        if market_ready(m) and get_asset_ids(m) and fee_rate(m) is not None:
            selected.append(m)
        if len(selected)>=a.markets:
            break
    if not selected:
        print('No eligible markets discovered; no synthetic events inserted.',file=sys.stderr)
        return 2
    ids=[asset for m in selected for asset in get_asset_ids(m)]
    # Public REST snapshot seeds state, no need to trust one websocket's startup order.
    def refresh():
        books={}
        for offset in range(0,len(ids),200):
            response=get_json(f'{CLOB}/books',payload=[{'token_id':item} for item in ids[offset:offset+200]])
            if not isinstance(response,list):
                raise RuntimeError('Invalid CLOB batch shape')
            for b in response:
                if isinstance(b,dict) and b.get('asset_id'):
                    books[str(b['asset_id'])]=b
        return books
    books=await asyncio.to_thread(refresh)
    if not books:
        print('No books from REST; refusing to assume liquidity.',file=sys.stderr)
        return 2
    bad=set()
    database=connect(a.db)
    last_sync=time.monotonic()
    last_eval=0.0
    last_event=time.monotonic()
    run_until=time.monotonic()+a.duration if a.duration>0 else float('inf')
    total=0
    print(f'Official market WSS: {URL}; markets={len(selected)}, asset_ids={len(ids)}; READ ONLY',flush=True)
    print(f'SQLite log: {a.db}. WS events are observations, not executed trades.',flush=True)
    try:
        while time.monotonic()<run_until:
            try:
                async with websockets.connect(URL,ping_interval=20,ping_timeout=20,max_size=5*1024*1024) as ws:
                    await ws.send(json.dumps({'type':'market','assets_ids':ids}))
                    next_heartbeat=time.monotonic()+10
                    while time.monotonic()<run_until:
                        now=time.monotonic()
                        if now>=next_heartbeat:
                            await ws.send('PING')
                            next_heartbeat=now+10
                        if now-last_sync>=a.resync:
                            fresh=await asyncio.to_thread(refresh)
                            books.update(fresh)
                            bad.clear()
                            last_sync=now
                        if now-last_eval>=a.evaluate_every:
                            observations=0
                            positive=0
                            for m in selected:
                                pair=get_asset_ids(m)
                                if pair and any(t in bad for t in pair):
                                    continue
                                entry=from_catalog(m,books)
                                if entry is None:
                                    continue
                                state,results=assess(entry,[D(a.capital)],D(a.adverse_cents)/100,a.max_age,a.max_skew)
                                if state['reason']=='accepted':
                                    observations+=1
                                    for r in results:
                                        if r['status']=='PAPER_POSITIVE':
                                            positive+=1
                                            if D(r['pnl'])>=D(a.min_alert):
                                                print(f"PAPER ONLY: ${r['pnl']:.4f} | {m.get('question','')[:65]}",flush=True)
                            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] paper snapshot: validated={observations}, positive={positive}, total WS events={total}",flush=True)
                            last_eval=now
                        try:
                            raw=await asyncio.wait_for(ws.recv(),timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if raw in ('PONG','PING'):
                            continue
                        for event in normalize_event(raw):
                            write_event(database,event)
                            total+=1
                            if apply_book_event(books,event):
                                bad.discard(str(event['asset_id']))
                            elif event['event_type']=='price_change':
                                # Force resync at next interval; don't use stale book levels.
                                for change in event.get('price_changes',[]):
                                    bad.add(str(change.get('asset_id','')))
                            elif event['event_type']=='market_resolved':
                                # Safest response is stop this session and rediscover.
                                print('Market resolved; stop stream and rediscover before evaluating.',file=sys.stderr)
                                return 3
            except (OSError,TimeoutError,RuntimeError,websockets.exceptions.WebSocketException) as exc:
                print(f'WebSocket disconnected: {exc!r}; retrying after 5s (no trades).',file=sys.stderr)
                await asyncio.sleep(5)
        return 0
    finally:
        database.close()


def main(argv=None):
    p=argparse.ArgumentParser(description='Official public Polymarket WebSocket capture. READ-ONLY')
    p.add_argument('--markets',type=int,default=10)
    p.add_argument('--pages',type=int,default=3)
    p.add_argument('--limit',type=int,default=100)
    p.add_argument('--capital',default='50')
    p.add_argument('--adverse-cents',default='0.2')
    p.add_argument('--db',default='reports/research.sqlite3')
    p.add_argument('--resync',type=int,default=20,help='seconds between consistent REST book refreshes')
    p.add_argument('--evaluate-every',type=int,default=10)
    p.add_argument('--max-age',type=float,default=60)
    p.add_argument('--max-skew',type=float,default=10)
    p.add_argument('--min-alert',default='0.50')
    p.add_argument('--duration',type=int,default=300,help='seconds; 0 = until Ctrl+C')
    a=p.parse_args(argv)
    if not (1<=a.markets<=100 and 1<=a.pages<=20 and 1<=a.limit<=500 and
            5<=a.resync<=3600 and 3<=a.evaluate_every<=3600 and a.duration>=0 and
            D(a.capital)>0 and D(a.min_alert)>=0 and D(a.adverse_cents)>=0):
        p.error('Invalid parameters')
    try:
        return asyncio.run(run(a))
    except (KeyboardInterrupt, RuntimeError) as exc:
        if isinstance(exc,RuntimeError):
            print('API failed; no data was fabricated: '+str(exc),file=sys.stderr)
            return 2
        print('Stopped (research only).')
        return 0

if __name__=='__main__':
    raise SystemExit(main())
