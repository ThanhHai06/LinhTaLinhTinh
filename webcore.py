"""Online research worker. Public Polymarket data; NO wallet, orders, or trading.

WS events prompt a rate-limited REST refresh, not alleged executable fills.
Scanner's conservative fee/depth/timestamp checks remain authoritative.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from contextlib import closing
import time
from typing import Any

from scanner import D, assess, collect_demo, collect_live, discover, get_asset_ids, serializable
from rewards import reward_rows
from store import write_scan, connect

WS_URL = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def clampint(name: str, default: int, minimum: int, maximum: int) -> int:
    try: n = int(os.getenv(name, default))
    except ValueError: raise ValueError(f'{name} must be an integer')
    if not minimum <= n <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return n


@dataclass(frozen=True)
class Config:
    mode: str
    interval: int
    pages: int
    limit: int
    ws_markets: int
    websocket: bool
    adverse_cents: Decimal
    capitals: tuple[Decimal, ...]
    max_age: float
    max_skew: float
    data_dir: str

    @classmethod
    def from_env(cls):
        mode = os.getenv('APP_MODE', 'live').strip().lower()
        if mode not in ('live', 'demo'):
            raise ValueError('APP_MODE must be live or demo')
        raw = os.getenv('CAPITALS', '10,50,100')
        try:
            capitals = tuple(D(c.strip()) for c in raw.split(','))
        except Exception as exc:
            raise ValueError('CAPITALS must be comma-separated dollar amounts') from exc
        if not capitals or len(capitals) > 10 or any(not c.is_finite() or c <= 0 or c > 1_000_000 for c in capitals):
            raise ValueError('CAPITALS must contain 1–10 finite positive values <= 1000000')
        adverse = D(os.getenv('ADVERSE_CENTS', '0.2'))
        if not adverse.is_finite() or not 0 <= adverse <= 10:
            raise ValueError('ADVERSE_CENTS must be in 0..10')
        return cls(
            mode=mode, interval=clampint('SCAN_INTERVAL', 90, 30, 3600),
            pages=clampint('SCAN_PAGES', 2, 1, 20), limit=clampint('SCAN_LIMIT', 60, 1, 500),
            ws_markets=clampint('WS_MARKETS', 12, 1, 100),
            websocket=os.getenv('WS_ENABLED', 'true').lower() in ('1','true','yes') and mode == 'live',
            adverse_cents=adverse, capitals=capitals,
            max_age=float(clampint('MAX_BOOK_AGE', 60, 5, 600)),
            max_skew=float(clampint('MAX_BOOK_SKEW', 10, 1, 120)),
            data_dir=os.getenv('DATA_DIR', './data'),
        )


class ResearchEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = str(Path(cfg.data_dir)/'observations.sqlite3')
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.trigger = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.running = False
        self.last_error = None
        self.last_attempt_utc = None
        self.last_success_utc = None
        self.ws_connected = False
        self.ws_events = 0
        self.ws_last_utc = None
        self.rewards_at = None
        self.rewards_error = None
        self.rewards: list[dict] = []
        self.results: list[dict] = []
        self.screened: list[dict] = []
        self.books_count = 0
        self.accepted_count = 0
        self.last_scan_id = None
        self.asset_ids: tuple[str, ...] = ()
        self.sequence = 0
        self.last_scan_tick = 0.0

    async def start(self):
        Path(self.cfg.data_dir).mkdir(parents=True, exist_ok=True)
        with closing(connect(self.db)):
            pass
        self.tasks.append(asyncio.create_task(self.scan_loop(),name='scan-loop'))
        if self.cfg.websocket:
            self.tasks.append(asyncio.create_task(self.ws_loop(),name='ws-loop'))
        self.tasks.append(asyncio.create_task(self.rewards_loop(),name='reward-loop'))

    async def close(self):
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]

    def _collect(self) -> tuple[dict,tuple[str,...]]:
        rows=collect_demo() if self.cfg.mode=='demo' else collect_live(self.cfg.pages,self.cfg.limit)
        accepted=[]; screened=[]
        for row in rows:
            state, results=assess(row,list(self.cfg.capitals),self.cfg.adverse_cents/100,
                                 self.cfg.max_age,self.cfg.max_skew)
            screened.append(state)
            accepted.extend(results)
        accepted.sort(key=lambda r:D(r.get('pnl','-999999')),reverse=True)
        catalog=[]
        for row in rows[:self.cfg.ws_markets]:
            pair=get_asset_ids(row['market'])
            if pair:
                catalog.extend(pair)
        doc={'mode':self.cfg.mode,
             'data_type':'SYNTHETIC DEMO — NOT REAL' if self.cfg.mode=='demo' else 'LIVE PUBLIC API SNAPSHOT — NO ORDERS',
             'generated_utc':utcnow(), 'markets_with_both_books':len(rows),
             'markets_accepted':sum(x.get('reason')=='accepted' for x in screened),
             'stress_cents_per_leg':str(self.cfg.adverse_cents),'screened':screened,
             'results':accepted}
        return doc,tuple(catalog)

    def _prune_scans(self, keep: int):
        """Bound SQLite growth on small cloud volumes; never sum rows into earned PnL."""
        with closing(connect(self.db)) as db, db:
            db.execute('DELETE FROM opportunities WHERE scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT -1 OFFSET ?)', (keep,))
            db.execute('DELETE FROM scans WHERE scan_id IN (SELECT scan_id FROM scans ORDER BY scan_id DESC LIMIT -1 OFFSET ?)', (keep,))

    async def run_scan(self) -> bool:
        if self.lock.locked():
            return False
        async with self.lock:
            self.running=True
            self.last_attempt_utc=utcnow()
            try:
                doc,asset_ids=await asyncio.to_thread(self._collect)
                sid=await asyncio.to_thread(write_scan,self.db,doc)
                if sid % 25 == 0:
                    await asyncio.to_thread(self._prune_scans, 3000)
                self.results=json.loads(json.dumps(doc['results'],default=serializable))
                self.screened=doc['screened']
                self.books_count=doc['markets_with_both_books']
                self.accepted_count=doc['markets_accepted']
                self.last_success_utc=doc['generated_utc']
                self.last_error=None
                self.last_scan_id=sid
                self.asset_ids=asset_ids
                self.sequence+=1
                return True
            except Exception as exc:
                self.last_error=f'{type(exc).__name__}: {str(exc)[:400]}'
                self.sequence+=1
                return False
            finally:
                self.last_scan_tick=time.monotonic()
                self.running=False

    async def scan_loop(self):
        while not self.stop_event.is_set():
            await self.run_scan()
            # Do not trigger repeated costly calls faster than configured cadence.
            try:
                await asyncio.wait_for(self.trigger.wait(),timeout=self.cfg.interval)
            except asyncio.TimeoutError:
                pass
            self.trigger.clear()
            elapsed=time.monotonic()-self.last_scan_tick
            if elapsed < 30:
                try: await asyncio.wait_for(self.stop_event.wait(),timeout=30-elapsed)
                except asyncio.TimeoutError: pass

    async def rewards_loop(self):
        while not self.stop_event.is_set():
            if self.cfg.mode=='demo':
                self.rewards=[]
                self.rewards_at=utcnow()
                self.rewards_error='Demo: no real reward allocations'
            else:
                try:
                    markets=await asyncio.to_thread(discover,self.cfg.pages,self.cfg.limit)
                    rows=reward_rows(markets)
                    self.rewards=rows[:100]
                    self.rewards_at=utcnow()
                    self.rewards_error=None
                except Exception as exc:
                    self.rewards_error=f'{type(exc).__name__}: {str(exc)[:200]}'
            try:
                await asyncio.wait_for(self.stop_event.wait(),timeout=900)
            except asyncio.TimeoutError: pass

    async def ws_loop(self):
        """Official public WS heartbeat and events; trigger rate-limited *REST* re-assessment.
        Never treat delta messages as complete, synchronized order books.
        """
        import websockets
        while not self.stop_event.is_set():
            if not self.asset_ids:
                await asyncio.sleep(2)
                continue
            subscribed=self.asset_ids
            try:
                async with websockets.connect(WS_URL,ping_interval=20,ping_timeout=20,
                                              max_size=5_000_000,open_timeout=15) as ws:
                    await ws.send(json.dumps({'type':'market','assets_ids':list(subscribed)}))
                    self.ws_connected=True
                    last_ping=0.0
                    last_trigger=0.0
                    while not self.stop_event.is_set():
                        now=time.monotonic()
                        if now-last_ping >= 10:
                            await ws.send('PING')
                            last_ping=now
                        if self.asset_ids and self.asset_ids != subscribed:
                            break  # resubscribe to refreshed catalog
                        try:
                            raw=await asyncio.wait_for(ws.recv(),timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if raw in ('PING','PONG'):
                            continue
                        try: events=json.loads(raw)
                        except (ValueError,TypeError): continue
                        if not isinstance(events,list): events=[events]
                        updates=0
                        for event in events:
                            if isinstance(event,dict) and event.get('event_type') in ('book','price_change','best_bid_ask'):
                                updates+=1
                        if updates:
                            self.ws_events+=updates
                            self.ws_last_utc=utcnow()
                            if now-last_trigger >= 30:
                                self.trigger.set()
                                last_trigger=now
            except (OSError,TimeoutError,websockets.exceptions.WebSocketException) as exc:
                self.last_ws_error=f'{type(exc).__name__}: {str(exc)[:180]}'
                await asyncio.sleep(5)
            finally:
                self.ws_connected=False

    def overview(self) -> dict:
        positives=[r for r in self.results if r.get('status')=='PAPER_POSITIVE']
        return {'mode':self.cfg.mode,'live_data_verified':self.cfg.mode=='live' and bool(self.last_success_utc),
                'last_attempt_utc':self.last_attempt_utc,'last_success_utc':self.last_success_utc,
                'last_error':self.last_error,'scanning':self.running,'sequence':self.sequence,
                'books_count':self.books_count,'accepted_count':self.accepted_count,
                'paper_positive':len(positives),
                'best_paper_pnl':max((float(r['pnl']) for r in positives),default=None),
                'ws_enabled':self.cfg.websocket,'ws_connected':self.ws_connected,
                'ws_events':self.ws_events,'ws_last_utc':self.ws_last_utc,
                'ws_error':getattr(self,'last_ws_error',None),
                'reward_pools':len(self.rewards),'reward_error':self.rewards_error,
                'capitals':[str(c) for c in self.cfg.capitals],
                'stress_cents_per_leg':str(self.cfg.adverse_cents),
                'interval_seconds':self.cfg.interval,'scan_id':self.last_scan_id,
                'disclaimer':'Read-only research: snapshots are not trades or executable profit.'}

    def history(self,limit=30) -> list[dict]:
        with closing(connect(self.db)) as db:
            rows=db.execute('SELECT scan_id,created_utc,mode,accepted,scanned,paper_positive FROM scans ORDER BY scan_id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(zip(('scan_id','created_utc','mode','accepted','scanned','paper_positive'),r)) for r in rows]
