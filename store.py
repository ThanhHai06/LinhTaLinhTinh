"""SQLite journal for research snapshots, not account transactions."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

SCHEMA = '''
CREATE TABLE IF NOT EXISTS scans (
 scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_utc TEXT NOT NULL,
 mode TEXT NOT NULL,
 accepted INTEGER NOT NULL,
 scanned INTEGER NOT NULL,
 paper_positive INTEGER NOT NULL,
 source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
 market_id TEXT NOT NULL, question TEXT NOT NULL, capital TEXT NOT NULL,
 shares TEXT, cash TEXT, taker_fees TEXT, stress_reserve TEXT,
 theoretical_pnl TEXT, status TEXT NOT NULL, price_yes TEXT, price_no TEXT,
 fee_source TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS opp_market ON opportunities(market_id, scan_id);
CREATE TABLE IF NOT EXISTS ws_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 received_utc TEXT NOT NULL, market_id TEXT, asset_id TEXT,
 event_type TEXT NOT NULL, payload_json TEXT NOT NULL
);
'''

def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(str(p))
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    return db

def write_scan(path: str | Path, document: dict) -> int:
    rows = document['results']
    with closing(connect(path)) as db, db:
        cur = db.execute('INSERT INTO scans(created_utc,mode,accepted,scanned,paper_positive,source) VALUES(?,?,?,?,?,?)',
            (document['generated_utc'],document['mode'],document['markets_accepted'],document['markets_with_both_books'],
             sum(r.get('status')=='PAPER_POSITIVE' for r in rows),document.get('data_type','')))
        sid = cur.lastrowid
        db.executemany('''INSERT INTO opportunities(scan_id,market_id,question,capital,shares,cash,taker_fees,
             stress_reserve,theoretical_pnl,status,price_yes,price_no,fee_source,payload_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [(
              sid,str(r.get('market_id','')),r.get('question',''),str(r.get('capital','')),
              str(r.get('shares','')),str(r.get('cash','')),str(r.get('taker_fees','')),
              str(r.get('stress_reserve','')),str(r.get('pnl','')),r['status'],
              str(r.get('avg_yes','')),str(r.get('avg_no','')),r.get('fee_source',''),
              json.dumps(r, default=str,ensure_ascii=False)) for r in rows])
        return sid

def write_event(db: sqlite3.Connection, event: dict):
    db.execute('INSERT INTO ws_events(received_utc,market_id,asset_id,event_type,payload_json) VALUES(?,?,?,?,?)',
       (datetime.now(timezone.utc).isoformat(),str(event.get('market','')),str(event.get('asset_id','')),
        str(event.get('event_type','unknown')),json.dumps(event,ensure_ascii=False)))
    db.commit()

def summary(path: str | Path) -> dict:
    with closing(connect(path)) as db, db:
        n=db.execute('SELECT COUNT(*) FROM scans').fetchone()[0]
        live=db.execute("SELECT COUNT(*) FROM scans WHERE mode='live'").fetchone()[0]
        hits=db.execute("SELECT COUNT(*) FROM opportunities WHERE status='PAPER_POSITIVE'").fetchone()[0]
        bymarket=db.execute("""SELECT market_id, MAX(question), COUNT(*) AS observations,
              SUM(CASE WHEN status='PAPER_POSITIVE' THEN 1 ELSE 0 END) AS positive
              FROM opportunities WHERE market_id <> '' GROUP BY market_id ORDER BY positive DESC LIMIT 25""").fetchall()
        events=db.execute('SELECT COUNT(*) FROM ws_events').fetchone()[0]
    return dict(scans=n,live_scans=live,paper_positive_rows=hits,ws_events=events,
                most_positive=[{'market_id':x[0],'question':x[1],'observations':x[2],'positive':x[3]} for x in bymarket],
                warning='Snapshot observations are neither trades nor a backtest of executable profit.')
