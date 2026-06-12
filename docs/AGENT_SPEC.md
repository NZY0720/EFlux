# EFlux Agent Specification

One schema describes every market participant — the built-in YAML roster and
external VPPs joining over the API validate against the **same** code path
(`src/eflux/simulator/agent_spec.py`). The machine-readable contract lives at
[`docs/agent_spec.schema.json`](agent_spec.schema.json); regenerate it with:

```bash
PYTHONPATH=src .env/bin/python -m eflux.cli agent-spec-schema > docs/agent_spec.schema.json
```

## 1. AgentSpec fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string (1–100 chars) | yes | Unique display name (trade tape, participants directory). Duplicates are rejected at load. |
| `agent` | `zi` \| `truthful` \| `gas` \| `reflective` | no (default `zi`) | Strategy kind. `reflective` = LLM-steered (see §3). |
| `seed` | int | no | RNG seed (defaults to `42 + roster index`). |
| `params` | object | no | DER portfolio — sparse `VPPParams` fields (see the JSON schema for all 19). Unknown keys are rejected (422 from `POST /vpps`, load failure for the YAML roster); known keys are type-checked. |
| `agent_params` | object | no | Constructor kwargs for the strategy class. For `reflective`, they go to the inner `TruthfulAgent`. |
| `persona` | `{name, prompt}` | no | **`reflective` only** — strategy brief appended to the LLM system prompt (`prompt` ≤ 600 chars). Rejected on other agent kinds. |

Typos in top-level keys fail loudly (`extra="forbid"`), so a misspelled
`parms:` cannot silently load.

### Key `params` fields

`pv_kw_peak`, `battery_kwh`, `battery_kw_max`, `battery_eta_rt`,
`load_kw_base`, `load_profile` (`residential|industrial|commercial|flat`),
`wind_kw_rated`, `wind_mean_speed`, `gas_kw_max`, `gas_cost_per_kwh`,
`markup_floor`, and site coords `pv_lat`/`pv_lon` (enable real Open-Meteo
weather for PV + wind; stripped when `EFLUX_PV_PHYSICAL=false`).

### Useful `agent_params` per kind

| Kind | Param | Effect |
|---|---|---|
| `truthful` / `reflective` | `demand_beta` (float, default 0) | Price-responsive bidding: bid = `price_ref * min(price_cap_mult, 1 + demand_beta * deficit_frac)`. With `0.5`, an urgent deficit bids up to 75 — crossing the gas merit order (55–72). |
| `truthful` / `reflective` | `price_cap_mult` (default 1.5) | Cap on the demand-responsive bid. |
| `zi` | `spread_frac` (default 0.5) | Half-width of the random price range. |
| `gas` | `quote_every_n_ticks` (default 30) | Re-quote cadence for the dispatchable ask. |

## 2. Built-in roster (`scenarios/default.yaml`)

Each entry under `vpps:` is one AgentSpec. Example:

```yaml
vpps:
  - name: llm-arb-aggressive
    agent: reflective
    seed: 78
    params: { pv_kw_peak: 2.0, battery_kwh: 30.0, battery_kw_max: 8.0, load_kw_base: 0.5,
              markup_floor: 0.4 }
    agent_params: { demand_beta: 0.5 }
    persona:
      name: aggressive-arbitrageur
      prompt: >-
        You are an aggressive spread-capture arbitrageur. ...
```

Loaded by `load_default_scenario()`; the roster file is set via
`EFLUX_SCENARIO_FILE` (default `scenarios/default.yaml`).

## 3. Reflective (LLM-steered) agents

All `agent: reflective` entries share **one** LLM connection (configured via
`EFLUX_REFLECTIVE_ENABLED`, `EFLUX_LLM_BASE_URL`, `EFLUX_LLM_MODEL`, `key.txt`):

- **Staggering** — the loader assigns each agent a reflection offset
  (`round(i * interval / n)`), so with 4 agents at a 60-tick interval they
  reflect at ticks 0/15/30/45. A shared semaphore guarantees at most one
  in-flight LLM call; a cycle that would overlap is *skipped* (counted on the
  agent as `skipped_count`), never queued.
- **Learning loop** — every reflection closes the previous hints' outcome
  window (PnL delta, trades, SOC over the window) and persists it as one JSONL
  record under `data/agent_memory/<name>.jsonl`. The last 5 outcomes — plus
  recent market-wide trades and the *other* LLM agents' latest reflections —
  are fed into the next prompt. The LLM may also return a `lesson` (≤160
  chars), a durable rule of thumb that survives backend restarts.
- **Fallback** — when the LLM is unconfigured or unreachable, reflective
  agents trade on their inner `TruthfulAgent` baseline and report
  `llm_status` accordingly. Nothing else changes.

Reflections are public: `GET /market/reflections` (the "Agent thoughts" panel).

## 4. External VPPs — joining over the API

External participants use the same `params` schema; today they are owned by a
user account (self-service agent registration is planned, not yet built).

### Connect flow

```bash
BASE=http://localhost:8000

# 1. Passwordless login (dev mode returns the token directly)
TOKEN=$(curl -s -X POST $BASE/auth/magic-link -H 'Content-Type: application/json' \
  -d '{"email":"agent-operator@example.com"}' | jq -r '.dev_token')
SESSION=$(curl -s -X POST $BASE/auth/consume -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\"}" | jq -r '.session_token')

# 2. (Optional, for long-lived bots) mint an API key — use it like the session token
KEY=$(curl -s -X POST $BASE/auth/api-keys -H "Authorization: Bearer $SESSION" \
  -H 'Content-Type: application/json' -d '{"name":"my-bot"}' | jq -r '.key')

# 3. Register the VPP — `params` is exactly the AgentSpec params block,
#    validated by the same code as the internal roster
VPP_ID=$(curl -s -X POST $BASE/vpps -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-external-vpp","params":{"pv_kw_peak":4.0,"battery_kwh":10.0}}' | jq -r '.id')

# 4. Trade
curl -s -X POST $BASE/orders -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"vpp_id\":$VPP_ID,\"side\":\"buy\",\"price\":49.5,\"qty\":1.0}"
```

### Order semantics

- Bounds: `0 < price ≤ 1000`, `0.01 ≤ qty ≤ 1000`.
- Matching: continuous double auction, price-time priority; fills happen at
  the **resting** order's price.
- **Realtime only**: external orders are rejected (409) while the market runs
  at 10x/100x speed.
- **TTL**: resting orders expire after `EFLUX_ORDER_TTL_SEC` sim-seconds
  (default 180) — an `order.cancelled` event is emitted. Re-quote rather than
  fire-and-forget. The submit response's `expires_at_sim` field tells you the
  exact sweep time (null when the TTL is disabled).
- Cancel via `POST /orders/cancel {"order_id": N}` — ownership enforced
  (404 on anything that isn't your resting order).

### Market data (public, no auth)

| Endpoint | Purpose |
|---|---|
| `GET /market/snapshot?depth=N` | Book depth, last price, speed, **balance** block (supply/demand KPI). |
| `GET /market/trades?limit=N` | Recent trades (backfill). |
| `GET /market/participants` | id → name/kind/strategy directory. |
| `GET /market/supply_curve` | Resting orders with per-VPP category attribution (merit order). |
| `GET /market/agents` | Live roster: endowments, PnL, SOC, power flows. |
| `GET /market/reflections?limit=N` | LLM agents' reflection feed (incl. lessons). |

### Streaming

`WS /ws/market` (token optional: `?token=<session-or-api-key>`) streams
`order.submitted`, `order.cancelled`, `trade`, and `tick` events — the same
payload shapes as the REST backfill endpoints. Reconnect + replay from
`GET /market/trades`.

## 5. Versioning notes

- Memory records carry `"v": 1`; future shape changes bump the version and
  readers skip records they don't understand.
- The JSON schema file is generated — regenerate it after changing
  `AgentSpec`, `PersonaSpec`, or `VPPParams`.
