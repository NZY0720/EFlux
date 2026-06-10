# EFlux — Agent-based VPP Electricity Trading Platform

VPP agents trade in a continuous double auction electricity market. Heterogeneous DER endowments (PV + battery + flexible load), online strategy learning (PPO + LLM reflection — later phases).

## Status

Backend bootstrap.

## Stack

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2 (async) + Postgres + Redis Streams
- **Market**: CDA limit order book, rolling clock with adjustable speed (1x/10x/100x)
- **Agents**: ZI baseline → Truthful → PPO (Ray RLlib, later) → Reflective (LLM, later)
- **Frontend**: React + Vite + ECharts (later)
- **Package mgmt**: `uv`

## Local Dev (macOS)

### 1. Prereqs

Install [Homebrew](https://brew.sh/) if you don't have it, then:

```bash
brew install python@3.12 node pnpm uv
```

### 2. Bootstrap

```bash
# venv lives in .env/ (non-standard, per project choice — uv defaults to .venv/)
export UV_PROJECT_ENVIRONMENT=.env
uv venv .env --python 3.12
uv sync --extra dev
cp config.env.example config.env   # defaults work for SQLite dev
```

### 3. Run

```bash
./tasks.sh dev                     # FastAPI with --reload on :8000
# or:
.env/bin/python -m uvicorn eflux.api.main:app --reload
```
Swagger UI: http://localhost:8000/docs

### 4. Smoke test (in another shell)
```bash
./tasks.sh smoke    # REST: magic-link → session → VPP create → aggressive order
./tasks.sh ws       # WebSocket: 5 live market events
```

### 5. Frontend (separate shell)
```bash
./tasks.sh fe-install   # one-time: pnpm install
./tasks.sh fe-dev       # Vite dev server on :5173 (proxies /api + /ws to backend)
```
Open http://localhost:5173/ — backend must be running on :8000.

Frontend stack: Vite + React 18 + TypeScript + Tailwind v4 + ECharts.
Pages: `Market` (live price chart + order book depth + trade tape), `My VPPs` (create/list + submit order).

### 6. One-click launcher
```bash
./tasks.sh start    # = ./scripts/start-all.sh
```
What it does:
- starts backend in the background (PID/log in `.run/backend.{pid,log}`)
- starts frontend in the background (PID/log in `.run/frontend.{pid,log}`)
- waits for both `/health` and `/` to respond
- opens your default browser to http://localhost:5173/

Stop everything: `./tasks.sh stop` (= `./scripts/stop-all.sh`).
Tail logs while running: `tail -f .run/backend.log` / `tail -f .run/frontend.log`.

### 7. Database migrations (alembic)
Schema lives in `alembic/versions/`. The dev path runs `Base.metadata.create_all` at startup so a fresh SQLite file just works, but for any non-dev environment (or whenever you change models) drive everything through alembic:

```bash
./tasks.sh migrate                        # = alembic upgrade head
./tasks.sh makemigration "add foo column" # autogenerate next migration
```

To exercise the migration-only path (production-style), set `EFLUX_AUTO_CREATE_SCHEMA=false` in `config.env` — lifespan will then refuse to create tables and you must run `./tasks.sh migrate` first.

### 8. Default scenario
On startup, 3 built-in ZI VPPs are loaded — solar-heavy, battery-heavy, load-heavy.
They trade against each other continuously. Connect your own VPPs via `POST /vpps` then `POST /orders`.

### Important notes
- venv is at `.env/`, NOT `.venv/`. uv defaults to `.venv/` — always export `UV_PROJECT_ENVIRONMENT=.env` first (the shell scripts already do this).
- Env vars file is `config.env` (not `.env`) to avoid clashing with the venv dir.
- `key.txt` holds the MiMo LLM key (gitignored). Not used until Phase 6.
- Speed lock: external (user-submitted) orders only allowed at `market_speed=1.0`. Fast modes are for training/replay.

## Project Layout

```
conf_1/
  .env/                # Python venv (non-standard name per project choice)
  config.env           # local env vars (gitignored)
  key.txt              # LLM API key (gitignored)
  pyproject.toml
  tasks.sh             # dev task runner (dev/run/smoke/ws/fe-dev/start/stop/...)
  scripts/
    start-all.sh       # backend + frontend in background + open browser
    stop-all.sh        # kill backend + frontend
    ws_smoke.py        # WS smoke test (direct backend)
    ws_smoke_proxy.py  # WS smoke test (via Vite proxy)
  src/eflux/
    api/               # FastAPI app, routers, WS handlers
    auth/              # passwordless email + API key
    db/                # SQLAlchemy models, session
    market/            # LOB matching engine, rolling clock, events
    vpp/               # VPP abstraction + DER models
    agents/            # ZI / Truthful / PPO / Reflective
    bridge/            # Redis Stream <-> WebSocket
    simulator/         # in-process runner
    config.py
    cli.py
  alembic/             # migrations
  tests/
```

## Postgres + Redis

Default dev setup uses SQLite (`eflux_dev.db` in repo root) and the in-process **InMemoryBus** (no Redis required). Both are configured via `config.env`.

### Switching to Postgres
```bash
brew install postgresql@16 && brew services start postgresql@16
# Edit config.env: comment out the SQLite EFLUX_DB_URL, uncomment the Postgres one.
./tasks.sh migrate    # alembic creates the schema on the new DB
```

### Real PV physics (Open-Meteo + pvlib)
By default `vpp.PV.output_kw()` is a diurnal-sine stub. Install the `data` extras to drive PV from real weather data via pvlib's `ModelChain`:
```bash
uv sync --extra data    # adds pvlib + pandas
./tasks.sh dev          # default solar VPP auto-uses HKU rooftop (22.28, 114.13)
```
Backend log should print `Fetching weather for lat=22.28 lon=114.13 …` on startup and cache parquet files under `data/cache/weather/`. Disable explicitly with `EFLUX_PV_PHYSICAL=false`. User-created VPPs (via the UI or `POST /vpps`) can opt in by passing `pv_lat` + `pv_lon` + (optional) `pv_tilt`, `pv_azimuth` in the params dict — the *MyVPPs* page has an "advanced" toggle that exposes these fields.

### Switching to Redis Streams (event bus)
By default events flow through `InMemoryBus` (in-process fan-out). For multi-process / replay / durable streams use Redis:
```bash
brew install redis && brew services start redis
# In config.env (or shell env): EFLUX_BUS_BACKEND=redis
./tasks.sh dev        # backend log should print "Using RedisStreamBus at ..."
```
If Redis is unreachable on startup, lifespan logs a warning and silently falls back to `InMemoryBus` so the app keeps working.

## License

TBD
