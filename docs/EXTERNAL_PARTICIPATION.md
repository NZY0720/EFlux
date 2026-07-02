# EFlux External Participation — Strategy, Policies & Specification

**Drafted:** 2026-06-30 · **Status:** design proposal (no implementation yet)
**Companion docs:** [`ARCHITECTURE_AND_DESIGN.md`](ARCHITECTURE_AND_DESIGN.md) (the "why" — agent layering, §3 distributed-agent vision, §9 sequence) · [`AGENT_SPEC.md`](AGENT_SPEC.md) (the participant `AgentSpec`/`params` schema and today's connect flow).

This document defines **how external users participate in EFlux**: the modes of
participation, what crosses the local↔cloud boundary in each, the operating
policies (fair evaluation, leaderboard, quotas, model-cost accounting, safety),
and a phased rollout. It is a *specification and policy* document — it commits to
a direction and is honest about what already exists versus what must be built. No
source changes accompany it.

---

## 1. Context & goals

EFlux is a real-time, agent-based VPP electricity-trading simulator: heterogeneous
VPPs (solar, wind, battery, flexible load, gas) trade through a continuous
double-auction matching engine. We now want to open the market to **external
participants** and have settled on the following framing.

**Primary goal — a benchmark / competition platform, operated like a product.**
The point of onboarding is *fair evaluation*: external agents trade in a shared,
reproducible market and are ranked on a **leaderboard**. Because it is open to
outside parties, it must also carry platform-grade controls: **rate limits,
model-cost accounting, and safety rails**.

**Design invariants (inherited, non-negotiable).** Every participation mode must
respect the existing authority hierarchy (`ARCHITECTURE_AND_DESIGN.md` §5–§6, §10):

- All orders, from any source, terminate at the **one `RiskGate`**
  (`src/eflux/agents/hybrid/risk.py`) and the **single matching engine**
  (`src/eflux/market/matching_engine.py`). No external path may bypass either
  (principles #7, #8).
- Slow/remote computation (LLM calls, remote policies) **never sits in the
  synchronous tick path** (principle #1). External agents are asynchronous by
  construction (`ARCHITECTURE_AND_DESIGN.md` §3).
- Free-text rationale is **audit/UI metadata, never execution logic** (principle #9).

**Non-goals.** This is not a production energy market and settles no real money.
It is not (yet) a multi-process distributed system — see the state constraint below.

**Hard constraint — market state is ephemeral.** Identity and VPP *definitions*
persist (`src/eflux/db/models.py`: `User`, `Session`, `ApiKey`, `VPP`), but **all
market state — the order book, trades, PnL, SOC — is in-memory and wiped on
backend restart**. Any leaderboard or evaluation result that must survive a
restart therefore needs durable result capture (the backtest runner already does
this; the live market does not — see §6, §8).

---

## 2. Baseline — what already exists

Almost all of the *substrate* for external participation is built. Every claim
below is backed by a concrete symbol so the rest of this doc can build on it
without re-deriving.

| Capability | Status | Where |
|---|---|---|
| Passwordless auth, sessions, long-lived API keys | ✅ built | `src/eflux/api/routers/auth.py`; `session_ttl_day=30` (`config.py`) |
| Per-user VPP ownership + isolation | ✅ built | `VPP.owner_id` (`db/models.py`); ownership enforced on every order (`routers/orders.py:48`, `:85`) |
| Self-service VPP creation (passive DER record) | ✅ built | `POST /vpps` → `validate_vpp_params` (`routers/vpps.py:220`, `simulator/agent_spec.py`) — **same schema as the built-in roster** |
| External order submit / cancel (realtime) | ✅ built | `POST /orders`, `POST /orders/cancel` (`routers/orders.py`) |
| Universal hard-constraint gate | ✅ built | `RiskGate.validate(...)` (`hybrid/risk.py`) |
| Public market data + live stream | ✅ built | `GET /market/*`, `WS /ws/market` (`routers/market.py`, `api/ws/market.py`) |
| Layered "brain" with a clean policy seam | ✅ built | `StrategyPolicy.select_action` (`strategy/policy.py`); `HybridPolicyAgent` (`hybrid/agent.py`) |
| Soft LLM guidance contract | ✅ built | `StrategyGuidance`, `MetaControl` (`reflective/strategist.py`) |
| Shared, staggered, one-in-flight LLM | ✅ built | `LLMStrategist.llm_gate` semaphore + `skipped_count` (`reflective/strategist.py:345`) |
| Structured-action RL env (single-agent vs. synthetic counterparty) | ✅ built | `VPPPrimitiveEnv` (`ppo/primitive_env.py`); obs/action codec (`ppo/primitive_encoding.py`) |
| Reproducible offline backtest with manifest | ✅ built | `src/eflux/backtest/runner.py` (writes `manifest.json` w/ `llm_calls`, `expected_llm_calls`, 10-char scenario `sha1`) |
| Canonical remote **Agent Protocol** / gateway | ❌ not built | designed only in `ARCHITECTURE_AND_DESIGN.md` §3, §9; **no `src/eflux/agents/remote/` module exists** |

**Precise scope of today's `RiskGate`** (so we don't overclaim): it enforces
price band, qty band (no dust/fat-finger), optional notional cap, **per-VPP
`max_open_orders` (256)** and **per-tick `max_new_orders_per_tick` (20)**
anti-spam, and battery-SOC feasibility. It does **not** enforce per-account rate
limits, cancel-rate limits, or ownership (ownership is the REST layer's job). Those
are gaps this spec must fill (§5.2, §5.6).

---

## 3. Participation modes

The organizing insight: **EFlux's internal architecture is already a stack of
clean seams, and each seam is a candidate network boundary.** "What should be
exchanged between the user's local system and our cloud?" is answered by *picking
which seam becomes the wire*. From thinnest-local to heaviest-local:

| Tier | Boundary — local side produces | Cloud runs | What stays on the user's machine | Reuses |
|---|---|---|---|---|
| **0** | (nothing — preferences only) | LLM + PPO + oracle + compiler + gate + match | nothing | `HybridPolicyAgent` |
| **A1** | `OrderIntent` / order batch | gate + match | the entire agent | today's `POST /orders` |
| **A2** | trains any RL learner locally | exposes obs+reward as a Gym-style env | the algorithm & weights | `VPPPrimitiveEnv` encoding + reward |
| **A3** | soft `StrategyGuidance` | PPO + compiler + gate + match | the LLM and its reasoning | `StrategyGuidance` / `MetaControl` |

These are **not exclusive** — they form a tiered menu we can offer simultaneously,
each fronted by the same Agent Protocol envelope (§4). The selected tiers for this
phase are **0, A1, A2, A3** (the intermediate "emit a `StrategyAction` primitive"
boundary is intentionally *excluded* — it adds a contract surface without serving a
distinct participant persona).

Each mode maps to a participant persona:

### Tier 0 — Cloud-hosted managed agent (lowest barrier)

- **For:** the *prompt/preferences strategist* — no code, no infra. Picks a DER
  endowment and a trading persona; the platform runs everything.
- **Crosses the boundary:** only configuration (DER `params`, `agent_params`,
  an LLM `persona`, and preference knobs). No live decisions cross.
- **Stays private:** nothing — by design, the platform sees and runs it all.
- **Reuses:** `HybridPolicyAgent` (LLM strategist + PPO executor + Truthful oracle
  + RiskGate), the `persona` brief (`AGENT_SPEC.md` §3), and the tunable preference
  fields — `VPPParams.risk_aversion / markup_floor / markup_ceiling`
  (`vpp/base.py:33`), `agent_params.demand_beta / price_ref` (`AGENT_SPEC.md` §1),
  and the soft `StrategyGuidance` knobs (`risk_budget`, `soc_target`).
- **Exists vs. build — read it carefully.** The *agent* is fully real, and a
  **read-only** managed-VPP API exists (`GET /vpps/managed`,
  `GET /vpps/managed/{id}/performance` → PnL, trades, reflections, LLM health —
  `routers/vpps.py:156`, `:184`). **But there is no self-service provisioning
  path.** `POST /vpps` only accepts `name` + `params` and creates a *passive*
  `is_external=True` record with **no agent attached** — it cannot request
  `agent: hybrid` or a `persona`, and the simulator does not autonomously drive it
  (`routers/vpps.py:23`, `:241`). Today's "managed VPPs" are built-in roster agents
  flagged `is_my_vpp` (`simulator/runner.py:228`), not user-provisioned ones. **So
  Tier 0, despite being the lowest barrier, requires real work** (§7 P0): a
  provisioning endpoint that instantiates a `HybridPolicyAgent` for an external
  user from their params + persona + preferences, plus the preferences UI.
  - **✅ Now implemented (P0, 2026-06-30).** `POST /vpps/managed` provisions a
    `HybridPolicyAgent` bound to the user (`scenarios.provision_managed_vpp`); reads are
    owner-scoped (`my_managed_vpps(owner_id)`); `PATCH /vpps/managed/{id}` tunes
    persona/preferences; `DELETE` removes it. Definitions persist (`vpps.is_managed` +
    `managed_config`, migration `0002`) and re-provision on restart
    (`api/main._rehydrate_managed_vpps`); a "Deploy a cloud-hosted agent" card on the My VPPs
    page drives create/tune/delete.
  - **✅ Tier-0 enhancements (2026-07-01).** Per-agent LLM model selection (curated menu via
    `SharedLLM.client_for` + `GET /vpps/models`); richer deploy params (wind, load profile);
    `lesson` made private (owner-only); and an **agent chatroom** (`GET /market/chatter`,
    `agents/reflective/chat.py`) where the LLM agents post casual small talk, each in its own
    model's voice — replacing the old public "Agent Thoughts" feed.
- **Leaderboard role:** the baseline tier — entrants who only tune knobs compete
  against code-bearing entrants on the same board (tagged by tier, §6).

### Tier A1 — Local agent, exchange orders (full control)

- **For:** the *quant / bot author* who wants total control and maximum privacy.
- **Crosses the boundary:** market observations out (read APIs / WS), order &
  cancel **batches** in. All decision logic is the user's.
- **Stays private:** the entire agent — model, strategy, weights, everything.
- **Reuses:** today's `POST /orders` path and the `RiskRejected`→HTTP mapping
  (`routers/orders.py:61`). This is the closest mode to what already works; an
  external bot can trade *today* via single orders.
- **Exists vs. build:** single-order submit/cancel exist.
  - **✅ Now implemented (P1, 2026-07-01).** Agent Protocol v1 `POST /orders/batch`
    (envelope: `protocol_version` / `idempotency_key` / `deadline`; per-item results;
    cancels-first), the `GET /orders/open` state read, per-account **idempotency** replay,
    and a per-account **token-bucket rate limit** (120 burst / 2·s⁻¹ → 429). See
    `AGENT_SPEC.md` §5. **Still to build:** Python SDK + MCP adapter (P2), per-participant
    audit segmentation.
- **Leaderboard role:** the open-class tier — bring any code.

### Tier A2 — Networked RL environment (train locally)

- **For:** the *RL researcher* who wants EFlux as a training environment for *any*
  algorithm (not just our built-in PPO).
- **Crosses the boundary:** `reset()` / `step(action)` semantics over the wire —
  observation + reward + done flow out, encoded actions flow in. The learner and
  its weights never leave the user's machine.
- **Stays private:** the RL algorithm and the trained policy.
- **Reuses:** the proven obs/action **encoding and reward shape** —
  `VPPPrimitiveEnv` already trains over the exact live pipeline (oracle → compiler
  → `RiskGate` → engine) with a stable codec (`primitive_encoding.py`: `OBS_DIM`,
  `ACTION_DIM`, `encode_obs`, `decode_action`) and the §7 reward weights
  (`primitive_env.py:50`). A trained checkpoint already transfers to live
  inference without action-semantics drift.
- **Exists vs. build — important distinction.** Today's `VPPPrimitiveEnv` is
  **single-agent against a *synthetic* counterparty (or replayed CAISO data)**,
  in-process. A *networked env against the live shared market* is a **new
  construction**: the env transport, and a decision about whether training runs
  against the live multi-agent market or against a per-user **sandboxed training
  market** (strongly preferred — see §5.4). The encoding and reward are reusable;
  the over-the-wire env and live-market wiring are to build. Also: training implies
  many fast `step()`s, which collides with the market's **realtime-only** external
  constraint (`config.market_speed`, `is_realtime`) — another reason training must
  use a separate, time-dilatable sandbox market, not the live 1× board.
- **Leaderboard role:** entrants submit a *trained checkpoint or live policy* that
  is then evaluated on held-out scenarios (§6); the training env itself is practice,
  not scored.

### Tier A3 — Local soft guidance (steer, don't execute)

- **For:** the *LLM/strategy author* who wants their own model to set strategy but
  doesn't want to run tick-by-tick infra.
- **Crosses the boundary:** periodic `StrategyGuidance` out of the user's local
  LLM (preferred/avoid modes, `risk_budget`, `soc_target`) — optionally `MetaControl`
  if they also steer learning. The platform's PPO executor + compiler + gate do the
  rest.
- **Stays private:** the user's LLM and its full reasoning; only the small,
  structured guidance object crosses (the free-text `execution_style`/`lesson` are
  audit-only).
- **Reuses:** the **existing** `StrategyGuidance` / `MetaControl` contracts and
  `apply_guidance()` semantics (`reflective/strategist.py`) — A3 simply relocates
  the strategist from in-cloud to the user's machine. Because guidance is already
  *soft* and clamped, an unavailable or malformed remote guidance safely degrades
  to baseline (it already does — `LLMStrategist` keeps prior guidance on failure).
- **Exists vs. build:** the contract and soft-application exist and are battle-tested
  on internal hybrids. **To build:** an *ingestion* endpoint that binds an external
  user's guidance to *their* managed VPP (which presupposes Tier 0 provisioning),
  authentication of the guidance source, and cadence/rate limits.
- **Leaderboard role:** a distinct tier — the same platform PPO executor for
  everyone, differentiated only by the user's guidance, isolating "strategy quality"
  from "execution engineering."

---

## 4. The exchange contract — "EFlux Agent Protocol v1" (specification)

All four tiers are fronted by **one canonical envelope** so the gateway is the
single trust/validation boundary and adapters (REST, WebSocket, SDK, MCP, Redis
Streams) are interchangeable (`ARCHITECTURE_AND_DESIGN.md` §3). This section
specifies the contract; it does not implement it.

### 4.1 Common envelope

Formalizes `ARCHITECTURE_AND_DESIGN.md` §3 "required protocol semantics":

```
protocol_version    # int; readers reject unknown majors (cf. AGENT_SPEC.md §5 "v":1)
agent_id            # the participant (account/API-key principal)
vpp_id              # the target VPP; ownership re-checked server-side every call
tick_id             # the market tick the message refers to (staleness detection)
idempotency_key     # dedupe retries; a replay returns the original result, not a new order
deadline            # wall-clock by which a response is useful (late-response policy)
nonce / replay_cursor  # ordering + resumable event replay after reconnect
```

Server-enforced limits travel *with* the contract (not just as docs): order `ttl`,
`max_orders_per_tick`, price/qty bands, per-account rate/quota, and the **late-
response** and **fallback** policies. The **audit record** for every accepted/
rejected message is part of the contract, not an afterthought (§5.6).

### 4.2 Per-tier payloads (envelope variations)

- **A1 — order batch:** `submit_orders_batch([{side, price, qty, ttl, dispatched?}])`,
  `cancel_orders([order_id])`, plus a read `get_vpp_state` / `get_open_orders`.
  Bounds mirror today's single-order path: `0 < price ≤ 1000`,
  `0.01 ≤ qty ≤ 1000` (`routers/orders.py:27`).
- **A2 — RL env:** `reset() → obs`, `step(action) → {obs, reward, terminated,
  truncated, info}` over the wire, using the existing `OBS_DIM`/`ACTION_DIM`
  encoding. Adds an explicit **step budget** field (§5.2).
- **A3 — guidance:** `put_guidance({preferred_modes, avoid_modes, risk_budget,
  soc_target, execution_style?, lesson?, meta_control?})` — exactly the parsed
  `StrategyGuidance` + `MetaControl` shape, clamped server-side on arrival.

**Invariant restated:** every payload above resolves to candidate intents (or a
guidance bias) that pass through the *same* `RiskGate` and matching engine. The
protocol changes *what the user sends*, never *who has final authority*.

---

## 5. Policies

Policy emphasis follows the goal (§1): fairness/leaderboard first, then the
platform controls that make an open competition safe to operate.

### 5.1 Fair evaluation & leaderboard

The headline policy — specified on its own in §6.

### 5.2 Rate limits & quotas

Today's only throttles are **per-VPP/per-tick** anti-spam inside `RiskGate`
(`max_open_orders`, `max_new_orders_per_tick`). A competition needs **per-account**
governance layered at the gateway:

- **Order/cancel rate** per account (not just per VPP) and per API key.
- **API request rate** on read + write endpoints.
- **RL-env step budget** per session/day (A2 training is cheap to abuse).
- **Concurrency caps:** max active VPPs per account; max in-flight A3 guidance
  refreshes (mirroring the internal one-in-flight LLM semaphore).
- **Cancel/replace ratio** ceiling (anti-spoofing; see §5.6).

### 5.3 Model-cost accounting

For tiers that consume the **platform LLM** (Tier 0 always; A3 only if a user opts
into platform inference instead of local): the configured provider
(`EFLUX_LLM_*`, `key.txt`) costs real tokens, and the strategist is a **shared,
one-in-flight** resource (`LLMStrategist.llm_gate`) refreshed on a cadence
(`reflective_interval_ticks=60`). Policy:

- **Token/refresh budget** per managed agent; metered and attributed per account.
- **Fair-share scheduling** of the shared LLM so one account can't starve others'
  guidance refreshes (extend the existing staggering, `AGENT_SPEC.md` §3).
- **Credits / cost ceiling** per account, with graceful degradation to scripted
  baseline when exhausted (the hybrid already falls back when the LLM is
  unavailable — reuse that path).
- A3-with-local-LLM incurs **zero** platform model cost — a deliberate incentive.

### 5.4 Safety rails

- `RiskGate` as the universal veto for *every* tier (already true; keep it true).
- Price/qty/SOC bands and notional caps as the backstop against pathological
  learned/remote actions (already calibrated generously, `hybrid/risk.py` docstring).
- **Training/live isolation:** A2 training must run in a **separate sandbox
  market**, never the live leaderboard market — both for integrity (no practising
  against live opponents' real orders) and because training needs time dilation the
  live realtime-only market forbids.
- **Quarantine / kill-switch:** deactivate a misbehaving account's VPPs
  (`DELETE /vpps/{id}` exists for self-service; an admin/forced variant is to build).
- **Ephemeral demo accounts** for low-stakes trial (sessions already TTL out at 30d;
  a shorter demo TTL is a config change).

### 5.5 Privacy & IP

The per-tier "what stays local" column (§3) *is* the privacy model — it's why we
offer A1/A2/A3 at all. Additional rules:

- **Never expose another participant's private artifacts** — weights, prompts,
  local-agent logic. Public market data (`/market/*`) is intentionally symmetric
  and equal-access; private performance/reflections are owner-scoped
  (`routers/vpps.py` managed endpoints check ownership).
- **Per-participant audit segmentation:** today the event bus is *global* (all
  events, no per-agent provenance partition). A competition needs per-account audit
  isolation so one entrant cannot reconstruct another's strategy from a firehose —
  a gap (§8).

### 5.6 Market integrity & anti-abuse

- **Self-trading** is already prevented *within* a VPP (matching engine). **Cross-
  account collusion / wash trading** between cooperating entrants is **not** detected
  — a real competition risk requiring post-hoc trade-graph analysis (§8).
- **Spoofing / quote-stuffing:** cap the cancel/replace-to-fill ratio and order churn
  per account (§5.2).
- **Speed parity:** external orders are realtime-only — rejected at 10×/100×
  (`submit_external` → 409; `config.is_realtime`). The **live leaderboard market must
  therefore run at 1×** so all external participants face identical latency/clearing.
- **Equal data access:** all entrants get the same public `/market/*` feed and WS;
  no privileged depth or latency tiers.

### 5.7 Identity & isolation

- Two auth tiers exist: short-lived **sessions** (UI) and long-lived **API keys**
  (bots), both ownership-scoped (`auth.py`). Keep API keys as the competition
  credential (revocable via `key_prefix`).
- **Per-account quarantine** and **per-account audit trail** are the isolation
  features still to build (§8).

---

## 6. Leaderboard & evaluation design

The core deliverable of the competition framing. Two complementary surfaces:

**(a) Offline standardized evaluation (authoritative ranking).** Reuse the
**backtest runner** (`src/eflux/backtest/runner.py`) — it already produces a
reproducible `manifest.json` stamping the scenario `sha1` hash, seed, and LLM-call
count, and replays fixed CAISO+weather windows. An entrant's submitted artifact
(A2 checkpoint, A3 guidance source, Tier-0 config, or an A1 bot run against a
recorded tape) is scored on **held-out scenarios** the entrant never saw. This is
the reproducible, restart-durable result path (the live market is not — §1).

**(b) Live shared-market board (exhibition / real-time).** Entrants trade the live
1× market; a running scoreboard reflects current PnL/score. Engaging, but
ephemeral and harder to make perfectly fair — treated as exhibition, with (a) as
the ranking of record.

**Scoring metric.** Build on the existing reward shape rather than raw PnL —
`primitive_env.py:50` already encodes the right economics: realized cashflow +
mark-to-market inventory − imbalance − battery-degradation − SOC-deviation − invalid/
excess-order penalties. Two adjustments for fairness:

- **Endowment normalization / handicapping** so a larger battery or PV peak doesn't
  trivially win — score relative to a *fair-value baseline* (the Truthful oracle's
  valuation of the same endowment is a natural reference) or fix endowments per
  scenario.
- **Risk adjustment** (e.g. score per unit of PnL variance) so reckless high-variance
  play doesn't dominate a single lucky window.

**Board structure.** One unified leaderboard with a **tier tag** (0 / A1 / A2 / A3)
rather than four siloed boards — lets a knob-tuner and a custom RL policy be compared
while still surfacing per-tier winners.

**Anti-gaming.** Held-out evaluation scenarios; rate/step-budget limits (§5.2);
collusion detection (§5.6); fixed seeds + scenario hashes for reproducibility and
dispute resolution.

---

## 7. Phased rollout

Maps onto `ARCHITECTURE_AND_DESIGN.md` §9 "External-agent support", **benchmarking
each step before increasing autonomy** (principle #10). Ordered by value-to-effort,
front-loading the lowest-barrier tier and the contract everything else depends on.

| Phase | Deliverable | Mostly exists? |
|---|---|---|
| **P0** | **Tier 0 provisioning**: endpoint to instantiate a `HybridPolicyAgent` for an external user from params+persona+preferences; preferences UI; reuse managed read APIs | ✅ **done** — provision / PATCH / DELETE + persistence (migration `0002`) + UI |
| **P1** | **Agent Protocol v1** (§4) + **batch** submit/cancel + **state/open-orders** read + idempotency + **per-account rate limits** + **per-account audit segmentation** (Tier A1) | ✅ **done** — protocol/batch/state/idempotency/rate-limit (`AGENT_SPEC.md` §5); audit segmentation still open |
| **P2** | **Python SDK** over the protocol, then an **MCP adapter** exposing the same gateway tools (`get_market_snapshot`, `submit_orders_batch`, …) | ✅ **done** — `eflux.sdk.EFluxClient` + `examples/market_maker.py` + `eflux.mcp.server` (7 tools); `AGENT_SPEC.md` §5 |
| **P3** | **Networked RL env** (Tier A2) over the wire + **sandbox training market** + step budgets; reuse the existing encoding/reward | codec+reward ✅, transport+sandbox ❌ |
| **P4** | **External `StrategyGuidance` ingestion** (Tier A3) bound to a user's managed VPP + source auth + cadence limits | contract ✅, ingestion ❌ |
| **P5** | **Leaderboard service** + standardized **evaluation harness** on the backtest runner (durable results, held-out scenarios, scoring) | backtest ✅, leaderboard ❌ |

Rationale for the order: P0 ships the lowest-barrier mode fastest and exercises
provisioning; P1 establishes the contract + governance every other tier needs; P2
makes the contract usable; P3/P4 add the differentiated tiers; P5 turns
participation into a ranked competition (it can begin in parallel once the backtest
harness is wrapped, since the offline path already exists).

---

## 8. Gaps & open decisions

**Implementation gaps this spec surfaces** (none block the spec; all block the build):

- No canonical Agent Protocol / gateway / `remote/` module (P1).
- No batch endpoints or server-side agent **state/open-orders** read (P1).
- No **per-account** rate limiting/quota or **model-cost metering** (§5.2–5.3).
- No **per-participant audit segmentation** — the event bus is global (§5.5).
- No **networked RL env** or **sandbox training market** (P3).
- No **Tier-0 provisioning** path — `POST /vpps` makes a passive record (§3 Tier 0).
- No **collusion/wash-trade detection** (§5.6).
- No **durable leaderboard** store — live market state is ephemeral (§1, P5).

**Decisions to make before/while building:**

1. **Scoring formula** — exact endowment-normalization and risk-adjustment terms (§6).
2. **One market or two** — does live trading and A2 training share infrastructure, or
   is training always a per-user sandbox? (This doc recommends **separate**, §5.4.)
3. **Platform vs. local model for A3** — do we allow A3 users to consume the platform
   LLM (metered, §5.3) or require local inference only?
4. **Credit/cost model** — free tiers, quotas, and whether model cost is ever billed.
5. **Durability** — is offline backtest-based ranking sufficient (recommended), or do
   we invest in persisting live-market results across restarts?

---

## 9. Summary

EFlux already has the identity, ownership, order, risk, agent-layering, RL-encoding,
and reproducible-backtest substrate for external participation. What this step adds
is the **strategy/policy/spec layer**: a four-tier participation menu (0 / A1 / A2 /
A3) chosen by *which architectural seam becomes the wire*, one canonical Agent
Protocol envelope terminating at the existing RiskGate + matching engine, the
platform-grade policies that make an open competition fair and safe, and a
leaderboard built on the backtest runner's reproducibility. The honest headline:
the *plumbing* mostly exists; the *contract, the per-account governance, the
provisioning, and the leaderboard* are what remain to build.
