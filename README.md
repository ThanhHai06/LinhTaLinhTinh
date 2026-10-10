# POLYSCOPE · Polymarket Online Research Dashboard v2.0

**Independent, unofficial research app** that uses Polymarket's **official public Gamma, CLOB and market WebSocket endpoints**. Not affiliated with, endorsed by, or operated by Polymarket.

**READ-ONLY / PAPER OBSERVATION:** This app does not connect wallets, collect private keys, authorize transactions, place real orders, or generate realized profits. It is not a profitable-trading claim.

## What this ONLINE edition adds

- A responsive dark web terminal at `/` with overview, opportunity scanner, liquidity pool explorer and scan history.
- A FastAPI application with password-gated web UI and JSON APIs.
- Autonomous server-side scheduler: runs on your online server, not your PC.
- Live Gamma market discovery + CLOB `/books` batch market depth from official public APIs.
- Real-time **official WebSocket notifications** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`), which prompt **rate-limited REST book reevaluation**, rather than treating individual price changes as atomic opportunities. Underlying scans are periodic, not subsecond HFT.
- Exact source timestamps and time-skew filters for YES/NO paired depth.
- Market-specific fees (if supported), order-size minimums, adverse execution stress reserve, and budget scenarios ($10 / $50 / $100 default).
- Liquidity reward funding metadata, visibly distinguished from any user's actual/potential payout.
- SQLite persisted scans and CSV export.
- Docker deployment and environment-based configuration. No secrets are shipped.

## What it does **NOT** do

1. Trade automatically, place orders or sign messages.
2. Promise profit or assume two orders are atomic. One side can fill while the other doesn't.
3. Backtest *realized* profit. Each row is a snapshot and overlapping snapshots must not be summed.
4. Estimate liquidity reward earnings from pool allocation: those are market-wide distributions.
5. Automatically fall back to fake/demo results if an external API is down.
6. Stay online without a running cloud server. **This project is ready to deploy; a public URL is not created automatically.**

## Quick local web preview (Windows)

Install Python >= 3.10. Extract the ZIP, open its folder and double-click `run_local.bat`. It installs dependencies and runs **synthetic demo** at:

**http://127.0.0.1:8000**

The demo is explicitly tagged **SYNTHETIC**, not actual Polymarket quotes. For local live public observations, in PowerShell:

```powershell
py -3 -m pip install -r requirements.txt
$env:APP_MODE='live'
$env:APP_ENV='local'
py -3 -m uvicorn webapp:app --host 127.0.0.1 --port 8000 --workers 1
```

It should then show **LIVE** only after successfully receiving real public API data. In API failure cases the UI shows an error and does not replace it with artificial profits.

## Full online deployment via Docker (cloud VPS / Railway / Render)

1. Put these project files in a **private Git repository** (do not commit `.env`).
2. Create a cloud web service from the repository using its `Dockerfile`. The service must keep **one instance and one Uvicorn worker**. This design uses in-process scan scheduling and SQLite, and is not yet a horizontally scalable multi-instance system.
3. Set environment variables:

   | Variable | Value / explanation |
   | --- | --- |
   | `APP_ENV` | `production` (mandatory for fail-closed auth) |
   | `APP_PASSWORD` | Generate your own random **16+ characters**; mandatory for production |
   | `APP_MODE` | `live` (default). `demo` produces only labeled synthetic data |
   | `DATA_DIR` | `/data` (set to persistent volume mount) |
   | `SCAN_INTERVAL` | `90` seconds (minimum 30) |
   | `SCAN_PAGES` | `2` |
   | `SCAN_LIMIT` | `60` |
   | `CAPITALS` | `10,50,100` (USDC scenario budgets) |
   | `ADVERSE_CENTS` | `0.2` adverse cents **per share per leg** |
   | `WS_ENABLED` | `true` |
   | `WS_MARKETS` | `12` |

4. Attach a persistent volume at `/data` if you want history to survive redeploys and restarts. The container startup prepares the mounted data directory and drops to an unprivileged user before running the server. Some free tiers have **ephemeral filesystems**, and some sleep when idle — these are **not** true 24/7 services.
5. Configure HTTPS through your cloud platform or reverse proxy. Never expose HTTP Basic over public plain HTTP.
6. Open the platform-provided HTTPS URL, login as username `admin` with the secret `APP_PASSWORD`.
7. Check `/health`, and that the UI indicates `LIVE API / PAPER ANALYSIS` **with a real successful scan timestamp**. A connected WebSocket alone is not proof the CLOB REST path works.

For a single rented Linux VPS, you can also use:

```bash
cp .env.example .env
# Edit .env and set a STRONG password. Don't commit it.
docker compose up --build -d
# Proxy HTTPS to localhost:8000 with Caddy/Nginx or use a secure tunnel.
```

**Important:** `compose.yaml` binds only to `127.0.0.1:8000` for safe local reverse proxy setup. Do not change to a publicly exposed unsecured HTTP endpoint.

## API routes

- `/` web dashboard (login required if APP_PASSWORD set; always required in production)
- `GET /health` simple health check (no auth; says nothing about feed uptime)
- `GET /api/state` live stream status, last success/error and KPI observations
- `GET /api/opportunities` read-only paper rows with filter query `capital`, `status_filter`, `search`, `limit`
- `GET /api/rewards` published market-wide pool allocation settings
- `GET /api/history` SQLite scan history
- `GET /api/export.csv` export last snapshot
- `POST /api/scan` queue an extra read-only scan, protected by Basic auth and `X-Scanner-Action: manual-refresh`

No account, balance, authentication-token, wallet, order-signing, withdrawal or execution endpoints exist.

## Data quality / scope

- The scanner only accepts markets with official YES/NO IDs, `active`, `acceptingOrders`, supported fee schedule, and both corresponding order books.
- Unknown or unsupported fees are **rejected**, never assumed to be zero. This can exclude many real markets. That is safer than producing misleading PnL.
- `PAPER_POSITIVE` means theoretical pair-edge under book depth and fee/stress assumptions. **It is not a trade**, a quote guarantee, real PnL, or proven executable arbitrage.
- WebSocket event volume is NOT executions. Book snapshots and delta events can arrive asynchronously. WS events only initiate periodic REST refresh.
- The reward pool section displays the market's publicly advertised USDC/day pool allocations. Its earnings column is deliberately not present.
- If no data or no opportunities appear, that can be a correct outcome. Do not make up opportunities to fill the dashboard.
- Historical raw rows are pruned to the latest 3,000 scans (pruning runs every 25 scans); for longer research horizons export regularly or migrate to managed PostgreSQL. Do not run two processes against a single SQLite scheduler.
- Market universe is **a configured sample** (by page limits), not a guarantee of exhaustive coverage of every open Polymarket event.
- Platform georestrictions and **local gambling/financial laws** still matter. This read-only data tool does not establish that real-money trading is legal in a jurisdiction. Never use proxies to bypass restrictions.

## Tests

```bash
python -m unittest discover -s tests -v
```

All integration-like tests run with synthetic fixtures and mock failures. In this build environment, external internet connections to Polymarket APIs cannot be verified, so an actual hosted cloud deployment and end-to-end live exchange integration are **not** claimed.

## Official documentation verified 10 October 2026

- https://docs.polymarket.com/market-data/discover-markets
- https://docs.polymarket.com/market-data/market-details
- https://docs.polymarket.com/market-data/prices-order-books
- https://docs.polymarket.com/market-data/realtime-data
- https://docs.polymarket.com/trading/fees
- https://docs.polymarket.com/programs/liquidity-rewards

### Render Blueprint (simpler cloud path)

`render.yaml` is included. Push the folder to GitHub, create **Render → New → Blueprint**, choose the repository and provide `APP_PASSWORD` when prompted. The blueprint declares a paid Starter web service and 1GB persistent disk, not an unmetered free instance. Check the current Render costs before approving. Render sets HTTPS and gives you the web URL after deploy.

### Railway alternative

Railway automatically builds `Dockerfile` from the repo. Add variables from `.env.example`, create a persistent volume mounted at `/data`, and under Networking click **Generate Domain**. The bundled Docker entrypoint handles root-owned volumes before dropping privileges. Railway deployment and billing require your own account.
