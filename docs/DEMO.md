# EFlux — 3-minute demo script

A click-by-click walkthrough for showing EFlux to an audience (judges, a class,
a colleague). Total time: ~3 minutes. Reset between runs with
`./tasks.sh stop && ./tasks.sh start` — the market is in-memory, so every run
starts from a clean book and rebuilds within seconds.

## Setup (before the audience arrives)

```bash
./tasks.sh start          # backend + frontend + opens http://localhost:5173/
```

Optional, for the full LLM story: put the MiMo key in `key.txt` and set
`EFLUX_REFLECTIVE_ENABLED=true` + `EFLUX_LLM_BASE_URL` + `EFLUX_LLM_MODEL` in
`config.env` before starting — the *Agent thoughts* panel then fills with live
reflections (one every ~minute). Without it the panel explains the agent is
running on its Truthful baseline, which is also fine to show.

Log in once (any email; the dev magic-link token autofills) so the speed
control is unlocked and *My VPPs* is ready.

## The walkthrough

### 1. Market page (~60s) — "this is a live electricity market"

Open `/`. Talk over the panels top-to-bottom:

- **KPI bar** — last price, best bid/ask. Point out prices hovering in the
  40–70 band and trades streaming in.
- **Merit order** (the money shot) — "Every block is one resting offer, cheapest
  first. Amber is solar, blue is wind — they're nearly free, so they form the
  floor. Violet is batteries arbitraging the middle. Red is gas — it tops out
  the stack at its marginal cost, 55–72. The dashed line is demand. Where they
  cross is where trades clear. This rebuilds live every two seconds."
- **Agent thoughts** — "One participant is steered by an LLM. Every minute it
  reviews its own PnL and the order book, and nudges its strategy — here's its
  reasoning, live."

### 2. Participants page (~45s) — "thirty autonomous agents"

Click **Participants** in the nav.

- "Solar households, wind farms, factories, commercial buildings, four gas
  generators — each with its own strategy. Zero-Intelligence agents quote
  randomly within rational bounds, Truthful agents quote marginal cost, gas
  offers capacity at cost."
- Sort by **PnL** — "who's winning". Sort by **Output now** — "who's producing
  vs consuming right now". Point at the battery SOC bars moving.
- Find the highlighted **my-llm-vpp** row — the LLM badge shows live health.

### 3. Trade against the market (~45s) — "and you can join"

Go to **My VPPs**:

- Create a VPP (one field: a name; PV/battery defaults are fine).
- Submit a buy at price ~80, qty 0.05 → instant fill against the cheapest ask.
- Back on **Market**, find your trade in the tape — buyer is your VPP, seller
  is whichever agent had the best offer (usually a solar or wind farm:
  merit order in action).

### 4. Optional flourishes (~30s)

- **Speed control** (KPI bar, needs login): flip to 10x — sim time accelerates,
  the order flow visibly speeds up; external orders are locked out (say why:
  fast modes are for training/replay). Flip back to 1x.
- **Resilience**: `kill $(cat .run/backend.pid)` mid-demo — the amber
  "reconnecting" banner appears instead of silently frozen charts; restart and
  the UI announces the market was reset, then recovers on its own.

## Talking points if asked

- **Matching**: continuous double auction, price-time priority, in-memory
  limit order book (`src/eflux/market/`).
- **Why do agents trade every ~10–30s?** 1-second ticks produce tiny energy
  deltas; each agent accumulates its untraded balance and quotes once it
  clears a minimum size.
- **Is the weather real?** Coastal wind farms and any VPP created with lat/lon
  use live Open-Meteo data (pvlib for PV); the rest run synthetic profiles.
  The data-source banner on the Market page shows exactly which is which.
- **What persists?** Users and VPP definitions (SQLite/Postgres). Orders,
  trades and PnL are in-memory by design — restart = fresh market.
