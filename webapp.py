"""Protected, read-only Polymarket dashboard. No private key handling or order endpoints."""
from __future__ import annotations
from contextlib import asynccontextmanager
import csv
import io
import os
import secrets
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from webcore import Config, ResearchEngine

BASE=Path(__file__).resolve().parent
security=HTTPBasic(auto_error=False)


def verify(creds: HTTPBasicCredentials|None=Depends(security)):
    secret=os.getenv('APP_PASSWORD','')
    if not secret and os.getenv('APP_ENV','local').lower() != 'production':
        return True
    if creds is None:
        raise HTTPException(status_code=401,detail='Login required', headers={'WWW-Authenticate':'Basic realm="Polymarket Research"'})
    if (not secret or not secrets.compare_digest(creds.username,'admin')
        or not secrets.compare_digest(creds.password,secret)):
        raise HTTPException(status_code=401,detail='Invalid credentials',headers={'WWW-Authenticate':'Basic realm="Polymarket Research"'})
    return True


def create_app(cfg:Config|None=None,enable_workers=True)->FastAPI:
    cfg=cfg or Config.from_env()
    if os.getenv('APP_ENV','local').lower()=='production' and len(os.getenv('APP_PASSWORD',''))<16:
        raise RuntimeError('APP_PASSWORD must be set to at least 16 characters in production')
    engine=ResearchEngine(cfg)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if enable_workers:
            await engine.start()
        yield
        if enable_workers:
            await engine.close()
    app=FastAPI(title='Polymarket Research Dashboard (Unofficial)',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.engine=engine

    @app.get('/health')
    async def health():
        return {'ok':True,'service':'polymarket-research','mode':cfg.mode}

    @app.get('/',dependencies=[Depends(verify)],include_in_schema=False)
    async def home():
        return FileResponse(BASE/'static'/'index.html')

    @app.get('/assets/app.js',dependencies=[Depends(verify)],include_in_schema=False)
    async def js():
        return FileResponse(BASE/'static'/'app.js',media_type='application/javascript')

    @app.get('/assets/style.css',dependencies=[Depends(verify)],include_in_schema=False)
    async def css():
        return FileResponse(BASE/'static'/'style.css',media_type='text/css')

    @app.get('/api/state',dependencies=[Depends(verify)])
    async def state():
        return engine.overview()

    @app.get('/api/opportunities',dependencies=[Depends(verify)])
    async def opps(request:Request, capital:str='',status_filter:str='',search:str='',limit:int=250):
        limit=max(1,min(limit,500))
        rows=engine.results
        if capital: rows=[r for r in rows if str(r.get('capital'))==capital]
        if status_filter: rows=[r for r in rows if r.get('status')==status_filter]
        if search:
            txt=search.lower()[:120]
            rows=[r for r in rows if txt in (str(r.get('question',''))+' '+str(r.get('market_id',''))).lower()]
        return {'mode':cfg.mode,'observed_utc':engine.last_success_utc,'rows':rows[:limit],
                'count':len(rows),'note':'Snapshots are NOT executable trades or realized PnL.'}

    @app.get('/api/rewards',dependencies=[Depends(verify)])
    async def reward_view():
        return {'mode':cfg.mode,'observed_utc':engine.rewards_at,'rows':engine.rewards,'error':engine.rewards_error,
                'note':'Daily reward pools are market-wide, NOT personal earnings.'}

    @app.get('/api/history',dependencies=[Depends(verify)])
    async def hist(limit:int=30):
        return {'rows':engine.history(min(max(1,limit),100))}

    @app.post('/api/scan',dependencies=[Depends(verify)])
    async def trigger_scan(request: Request):
        if request.headers.get('X-Scanner-Action')!='manual-refresh':
            raise HTTPException(status_code=403,detail='Missing same-origin action header')
        engine.trigger.set()
        return {'queued':True,'note':'Rate-limited by the background worker.'}

    @app.get('/api/export.csv',dependencies=[Depends(verify)])
    async def export():
        keys=['market_id','question','capital','status','shares','avg_yes','avg_no','cash',
              'taker_fees','stress_reserve','pnl','return_pct','fee_rate','fee_source','url']
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=keys,extrasaction='ignore')
        writer.writeheader();writer.writerows(engine.results)
        return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={
            'Content-Disposition':'attachment; filename="paper_observations.csv"'})

    return app

app=create_app()
