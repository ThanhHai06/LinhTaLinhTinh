#!/usr/bin/env python3
"""Read-only Polymarket YES+NO book scanner and theoretical capital simulator.

No authentication, wallet keys, live orders, or profit guarantees.
Stdlib-only Python >=3.10. All prices are USDC/share.
"""
from __future__ import annotations
import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from store import write_scan, summary as db_summary

D = lambda value: Decimal(str(value))
ZERO = D(0)
CENTISH = D('0.01')
GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
USER_AGENT = 'Polymarket-Opportunity-Research/0.1 (read-only; no order placement)'


def get_json(url: str, *, payload: Any = None, tries: int = 3, timeout: int = 12) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    last = None
    for attempt in range(tries):
        try:
            req = Request(url, data=body, headers=headers, method='POST' if body is not None else 'GET')
            with urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last = exc
            if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                break
            if attempt < tries - 1:
                time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(f'API request failed: {url}: {last}')


def parse_array(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (ValueError, TypeError):
            return []
    return []


def get_asset_ids(m: dict) -> tuple[str, str] | None:
    version = str(m.get('version') or '').lower()
    # Strict official v1/v2 identity; missing version is not safe to assume.
    if version == 'v2':
        ids = parse_array(m.get('positionIds'))
    elif version in ('v1', 'ctf'):
        ids = parse_array(m.get('clobTokenIds'))
    else:
        return None
    labels = parse_array(m.get('outcomes'))
    if len(ids) != 2 or len(labels) != 2:
        return None
    if [str(v).lower() for v in labels] != ['yes', 'no']:
        return None
    if not all(isinstance(v, (str, int)) and str(v).isdigit() for v in ids):
        return None
    return str(ids[0]), str(ids[1])


def market_ready(m: dict) -> bool:
    return (m.get('active') is True and m.get('closed') is False
            and m.get('acceptingOrders') is True
            and m.get('enableOrderBook', True) is not False
            and m.get('archived', False) is not True
            and m.get('restricted', False) is not True)


def fee_rate(m: dict) -> tuple[Decimal, str] | None:
    """Use Gamma-specific fee parameters if supported; otherwise cautious fallback.

    None means unknown unsupported schedule, do not evaluate.
    """
    sched = m.get('feeSchedule')
    enabled = m.get('feesEnabled')
    if isinstance(sched, dict):
        try:
            exp = D(sched.get('exponent', 1))
            if exp != 1 or sched.get('takerOnly') is False:
                return None
            rate = D(sched['rate'])
            if not ZERO <= rate <= D('0.20'):
                return None
            if enabled is False:
                return ZERO, 'Gamma feesEnabled=false'
            return rate, 'Gamma feeSchedule'
        except (KeyError, ValueError, TypeError):
            return None
    if enabled is False:
        return ZERO, 'Gamma feesEnabled=false'
    return None  # fail closed: never assign an unverified fee category


@dataclass(frozen=True)
class Level:
    price: Decimal
    shares: Decimal


def parse_book(m: dict) -> tuple[list[Level], Decimal, float | None, str]:
    levels = []
    for row in m.get('asks', []):
        try:
            price, shares = D(row['price']), D(row['size'])
            if ZERO < price < 1 and shares > ZERO:
                levels.append(Level(price, shares))
        except (KeyError, TypeError, ValueError):
            continue
    levels.sort(key=lambda v: v.price)  # API commonly supplies worst price first
    min_shares = D(m.get('min_order_size', 0) or 0)
    stamp = m.get('timestamp')
    ts_seconds = None
    if stamp is not None:
        try:
            raw = float(stamp)
            ts_seconds = raw / 1000 if raw > 1e11 else raw
        except (TypeError, ValueError):
            pass
    return levels, min_shares, ts_seconds, str(m.get('asset_id', ''))


def depth_cost(levels: list[Level], q: Decimal, rate: Decimal) -> tuple[Decimal, Decimal] | None:
    left = q
    cost, fee = ZERO, ZERO
    for level in levels:
        take = min(left, level.shares)
        if take > ZERO:
            cost += take * level.price
            fee += take * rate * level.price * (1 - level.price)
            left -= take
        if left <= ZERO:
            return cost, fee
    return None


def breakpoints(levels: list[Level]) -> list[Decimal]:
    total = ZERO
    ret = []
    for lv in levels:
        total += lv.shares
        ret.append(total)
    return ret


def max_budget_qty(yes: list[Level], no: list[Level], fee: Decimal, budget: Decimal, adverse: Decimal = ZERO) -> Decimal:
    max_qty = min(sum((v.shares for v in yes), ZERO), sum((v.shares for v in no), ZERO))
    if max_qty <= ZERO:
        return ZERO
    def total(q: Decimal) -> Decimal:
        yc = depth_cost(yes, q, fee)
        nc = depth_cost(no, q, fee)
        if yc is None or nc is None:
            return D('1e30')
        return yc[0] + yc[1] + nc[0] + nc[1] + 2 * q * adverse
    lo, hi = ZERO, max_qty
    for _ in range(65):
        mid = (lo + hi) / 2
        if total(mid) <= budget:
            lo = mid
        else:
            hi = mid
    return lo.quantize(CENTISH, rounding=ROUND_DOWN)


def evaluate(yes: list[Level], no: list[Level], budget: Decimal,
             rate: Decimal, min_notional: Decimal, min_yes_shares: Decimal,
             min_no_shares: Decimal, adverse: Decimal) -> dict | None:
    """Maximize theoretical PnL over order-book piecewise-linear segments.

    Does not execute real orders. All pairs treated as settling to 1 USDC in total.
    Require both legs' minimum USDC notional and min shares. The adverse price
    term is a stress reserve, not a statistical fill probability estimate.
    """
    maxq = max_budget_qty(yes, no, rate, budget, adverse)
    if maxq <= ZERO:
        return None
    candidates = {maxq}
    for q in breakpoints(yes) + breakpoints(no):
        v = min(q, maxq).quantize(CENTISH, rounding=ROUND_DOWN)
        if v > ZERO:
            candidates.add(v)
    best = None
    for q in sorted(candidates):
        if q < min_yes_shares or q < min_no_shares:
            continue
        y, n = depth_cost(yes, q, rate), depth_cost(no, q, rate)
        if y is None or n is None:
            continue
        cash = y[0] + n[0] + y[1] + n[1]
        if y[0] < min_notional or n[0] < min_notional or cash + 2*q*adverse > budget:
            continue
        stress = 2 * q * adverse  # adverse execution movement on BOTH legs
        pnl = q - cash - stress
        if best is None or pnl > best['pnl']:
            best = {
                'shares': q, 'cash': cash, 'gross_cost': y[0] + n[0],
                'taker_fees': y[1] + n[1], 'stress_reserve': stress,
                'pnl': pnl, 'return_pct': (pnl / cash * 100) if cash else ZERO,
                'avg_yes': y[0] / q, 'avg_no': n[0] / q,
            }
    return best


def discover(page_count: int, page_limit: int) -> list[dict]:
    found = []
    cursor = None
    for _ in range(page_count):
        query = {'closed': 'false', 'limit': str(page_limit)}
        if cursor:
            query['after_cursor'] = cursor
        res = get_json(f'{GAMMA}/markets/keyset?{urlencode(query)}')
        if isinstance(res, dict):
            batch = res.get('markets', [])
            cursor = res.get('next_cursor')
        elif isinstance(res, list):
            batch = res
            cursor = None
        else:
            raise RuntimeError('Unexpected Gamma market list format')
        if not isinstance(batch, list):
            raise RuntimeError('Gamma markets is not a list')
        found.extend([m for m in batch if isinstance(m, dict)])
        if not cursor:
            break
        time.sleep(0.25)
    return found


def collect_live(pages: int, limit: int) -> list[dict]:
    markets = discover(pages, limit)
    picked = []
    for m in markets:
        ids = get_asset_ids(m)
        if market_ready(m) and ids and fee_rate(m) is not None:
            picked.append((m, ids))
    tokens = [token for _, pair in picked for token in pair]
    books = {}
    for i in range(0, len(tokens), 200):
        payload = [{'token_id': v} for v in tokens[i:i+200]]
        response = get_json(f'{CLOB}/books', payload=payload)
        if not isinstance(response, list):
            raise RuntimeError('Unexpected CLOB books response')
        books.update({str(b.get('asset_id')): b for b in response if isinstance(b, dict)})
        time.sleep(0.15)
    return [dict(market=m, yes=books.get(ids[0]), no=books.get(ids[1]))
            for m, ids in picked if ids[0] in books and ids[1] in books]


def collect_demo() -> list[dict]:
    now = str(int(time.time() * 1000))
    def mk(mid: int, yeslevels: list, nolevels: list, rate: float = .04, minsize: int = 5):
        ids = (str(mid * 10), str(mid * 10 + 1))
        m = {'id': str(mid), 'version': 'v2', 'positionIds': list(ids),
             'outcomes': '["Yes","No"]', 'question': f'FAKE demo market {mid}',
             'slug': f'fake-demo-{mid}', 'active': True, 'closed': False,
             'acceptingOrders': True, 'enableOrderBook': True,
             'feesEnabled': True, 'feeSchedule': {'rate': rate, 'exponent': 1,
                                                 'takerOnly': True}, 'orderMinSize': minsize}
        def book(id_, rows):
            return {'asset_id': id_, 'asks': [{'price': str(p), 'size': str(s)} for p,s in rows],
                    'min_order_size': '5', 'timestamp': now}
        return {'market': m, 'yes': book(ids[0], yeslevels), 'no': book(ids[1], nolevels)}
    return [
        mk(1, [(.42, 100), (.48, 200)], [(.53, 100), (.58, 200)]),
        mk(2, [(.49, 100)], [(.51, 100)]),
        mk(3, [(.38, 4), (.59, 100)], [(.52, 4), (.59, 100)]),
    ]


def assess(row: dict, capitals: list[Decimal], adverse: Decimal,
           age_limit: float, skew_limit: float, now: float | None = None) -> tuple[dict, list[dict]]:
    m = row['market']
    yes_raw, no_raw = row['yes'], row['no']
    basic = {'market_id': str(m.get('id','')), 'question': str(m.get('question','')),
             'slug': str(m.get('slug','')), 'url': f"https://polymarket.com/event/{m.get('slug','')}",
             'version': str(m.get('version', 'v1'))}
    errors = []
    if not yes_raw or not no_raw:
        errors.append('missing_order_book')
    if not market_ready(m):
        errors.append('not_trade_ready')
    ids = get_asset_ids(m)
    if not ids:
        errors.append('unsupported_outcomes')
    rate_record = fee_rate(m)
    if rate_record is None:
        errors.append('unsupported_fee_schedule')
    if errors:
        return dict(basic, reason=','.join(errors)), []
    yes, y_min, y_ts, yes_id = parse_book(yes_raw)
    no, n_min, n_ts, no_id = parse_book(no_raw)
    if not yes or not no:
        errors.append('empty_ask_depth')
    if yes_id != ids[0] or no_id != ids[1]:
        errors.append('book_id_mismatch')
    condition_id = str(m.get('conditionId', '') or '').lower()
    for raw in (yes_raw, no_raw):
        raw_condition = str(raw.get('market','') or '').lower()
        if condition_id and raw_condition and raw_condition != condition_id:
            errors.append('book_market_mismatch')
            break
    clock = time.time() if now is None else now
    if y_ts is None or n_ts is None:
        errors.append('missing_book_timestamp')
    elif (max(abs(clock - y_ts), abs(clock - n_ts)) > age_limit or
          abs(y_ts - n_ts) > skew_limit or y_ts > clock+2 or n_ts > clock+2):
        errors.append('stale_or_unsynchronized_books')
    if errors:
        return dict(basic, reason=','.join(errors)), []
    rate, fee_source = rate_record
    minimum = D(m.get('orderMinSize', 5) or 5)
    results=[]
    for cap in capitals:
        estimate = evaluate(yes, no, cap, rate, minimum, y_min, n_min, adverse)
        if estimate is None:
            results.append({**basic, 'capital': cap, 'status': 'INFEASIBLE',
                            'reason': 'budget_or_min_order_or_depth', 'fee_source': fee_source,
                            'fee_rate': rate})
            continue
        results.append({**basic, 'capital': cap, 'status': 'PAPER_POSITIVE' if estimate['pnl'] > ZERO else 'PAPER_NEGATIVE',
                        'reason': 'theoretical_only_no_orders_sent',
                        'fee_source': fee_source, 'fee_rate': rate, **estimate})
    return dict(basic, reason='accepted'), results


def serializable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(type(obj).__name__)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Polymarket READ-ONLY price scanner; no wallet or trading')
    p.add_argument('--mode', choices=['demo','live'], default='demo')
    p.add_argument('--capitals', default='10,50,100', help='comma-separated budgets in USDC')
    p.add_argument('--adverse-cents', default='0.2', help='stress reserve in US cents PER LEG per share')
    p.add_argument('--pages', type=int, default=2, help='Gamma pages (live)')
    p.add_argument('--limit', type=int, default=50, help='markets per page (live)')
    p.add_argument('--max-book-age', type=float, default=60)
    p.add_argument('--max-book-skew', type=float, default=10)
    p.add_argument('--output', default='reports/scan')
    p.add_argument('--db', default='', help='optional SQLite journal path')
    p.add_argument('--only-positive', action='store_true', help='print positive paper estimates only')
    a = p.parse_args(argv)
    try:
        caps = [D(x.strip()) for x in a.capitals.split(',')]
        adverse = D(a.adverse_cents) / 100
        if not caps or min(caps) <= ZERO or not ZERO <= adverse <= D('.1'):
            raise ValueError('invalid capitals or stress reserve')
        if not 1 <= a.pages <= 20 or not 1 <= a.limit <= 500:
            raise ValueError('pages must be 1..20 and limit 1..500')
        if not 0 < a.max_book_age <= 86400 or not 0 < a.max_book_skew <= 86400:
            raise ValueError('invalid book age or skew')
    except (ValueError, ArithmeticError) as e:
        p.error(str(e))
    try:
        rows = collect_demo() if a.mode == 'demo' else collect_live(a.pages, a.limit)
    except RuntimeError as e:
        print('LIVE API FAILED. Nothing has been fabricated. Try --mode demo offline.', file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 2
    allresults=[]
    discarded=[]
    for item in rows:
        state, res = assess(item, caps, adverse, a.max_book_age, a.max_book_skew)
        discarded.append(state)
        allresults.extend(res)
    allresults.sort(key=lambda x: D(x.get('pnl', '-1e50')), reverse=True)
    output=Path(a.output)
    output.parent.mkdir(exist_ok=True, parents=True)
    result_path=output.with_suffix('.json')
    csv_path=output.with_suffix('.csv')
    doc={'mode': a.mode, 'data_type': 'SYNTHETIC NOT REAL' if a.mode == 'demo' else 'PUBLIC API SNAPSHOT (NOT EXECUTED TRADES)',
         'generated_utc': datetime.now(timezone.utc).isoformat(), 'markets_with_both_books': len(rows),
         'markets_accepted': sum(s['reason']=='accepted' for s in discarded),
         'stress_cents_per_leg': str(a.adverse_cents),
         'notes': ['A positive PAPER result is not executable profit.',
                   'No trade sent; books are not atomically executable; one leg might fill alone.',
                   'Full pairs are valued at $1 only at redemption/settlement, which can be delayed.',
                   'Geo restrictions and local legality must be checked before any live trading.',
                   'MinOrderSize interpretation follows Gamma USDC min and book share min conservatively.'],
         'screened': discarded, 'results': allresults}
    result_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=serializable), encoding='utf-8')
    fields = ['capital','market_id','question','version','status','shares','avg_yes','avg_no','cash','taker_fees',
              'stress_reserve','pnl','return_pct','fee_rate','fee_source','reason','url']
    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:
        w=csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for row in allresults:
            w.writerow(row)
    if a.db:
        sid=write_scan(a.db,doc)
        print(f'SQLite journal: {a.db}, scan_id={sid}')
    print(f'MODE: {a.mode.upper()} | markets with both books={len(rows)}, accepted={doc["markets_accepted"]}')
    print('Capital   Status          PnL theoretical   Market')
    for r in ([r for r in allresults if r['status']=='PAPER_POSITIVE'] if a.only_positive else allresults)[:25]:
        pnl = r.get('pnl')
        print(f"${str(r['capital']):<8} {r['status']:<15} {('$'+str(round(pnl, 4))) if pnl is not None else 'N/A':<17} {r['question'][:55]}")
    print(f'JSON: {result_path.resolve()}')
    print(f'CSV:  {csv_path.resolve()}')
    print('READ ONLY. No money traded. Results are not proof of an executable edge.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
