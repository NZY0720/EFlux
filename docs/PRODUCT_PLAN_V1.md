# EFlux Platform v1 — Final Product & Engineering Plan

**Date:** 2026-07-10 · **Status:** approved direction (supersedes the 2026-07-10 draft productization plan)
**Positioning:** an electricity-market proving ground with two audiences — (1) agent builders competing on fixed scenarios, and (2) **real electricity traders validating what they could earn with the endowment they actually have** (paper trading + point-in-time replay; no real-money execution in v1).

---

## 1. What changed vs. the draft plan

| # | Change | Why |
|---|--------|-----|
| 1 | One release → **three phases** (A: product core + managed track + prove-out; B: Container Standard; C: Container Model) | Container infra is the longest, least locally-verifiable pole (no Docker locally; staging-only). Phase A ships user value in weeks, and every A deliverable is a hard prerequisite of B/C anyway. |
| 2 | **Standard-track tick math fixed** | Draft: 24h @ 60s windows × 500ms deadline = 1,440 ticks × 0.5s = 12 min of pure deadline budget vs a 15-min pod cap — compliant bots would bust the cap. Now: 5-min windows (288 ticks) and a derived cap (§6). |
| 3 | **Seed-leak defense added** | Fixed hidden seeds + visible own-replays allow probe-then-overfit (record observations, then submit a lookup table). Quantopian's own research showed backtest scores don't predict out-of-sample; Numerai obfuscates data and scores forward. Now: rotating seed pools, holdout seeds, submission cooldowns, replay embargo (§6). |
| 4 | **Image cap 1GB → 4GB (Standard track)** | 1GB compressed excludes even a 7B Q4 model, contradicting "local weights allowed". Model track stays 1GB (weights live behind the Model Proxy). |
| 5 | **Role model added (`User.role`)** | `POST /market/ppo/renew` is callable by *any* authenticated user; no admin concept exists anywhere. UI removal alone doesn't fix authz. |
| 6 | **Acceptance criteria repaired** | "Suite remains 452 passed" was wrong (actual: 451+1 skipped) and freezes test growth. "CI < 3 min" referenced a CI that doesn't exist. Now: no-regression + new-surface-covered, and a two-tier CI (§9). |
| 7 | **Real-trader "Prove-out" tier added** | New positioning (§4). |
| 8 | **Forecast-quality workstream added** | The Forecast Hub was producing provably bad output; fixed 2026-07-10 (§8). Trust in forecasts is a precondition for the trader audience. |
| 9 | Numeric aesthetic dials removed | "Variation 6 / motion 4 / density 7" are unmeasurable; the concrete rules (§5) already do the job. |

Verified current state (2026-07-10 audit): 27→17 ruff issues; suite 465 passed (~33s natively); frontend one 470.6KB-gzip chunk, no route splitting, eager ECharts; `MyVPPs.tsx` 1,492 lines; Inter declared but never loaded; localStorage Bearer auth, no `/auth/me`; magic link = dev token echo (no email); SQLite default with Postgres URL + 3 alembic migrations ready; optional Redis Streams bus with in-memory fallback; working Python SDK + examples; **no CI**; no competition/evaluation/k8s/registry code.

## 2. What we learned from comparable products

- **PowerTAC** (powertac.org): the academic gold standard for power-trading agent competitions — open-source simulator + broker API, many game instances to average luck, and market-power discouraged by capacity/imbalance fees (EFlux's imbalance settlement is already aligned). Lesson: publish the simulator + local harness so participants self-serve; PowerTAC never productized, which is our opening.
- **QuantConnect / LEAN**: the winning loop is **backtest → paper → live on the same engine and API** (200k+ live algos). Freemium tiers, open-source engine, community strategy library. Lesson: sandbox / evaluation / prove-out must speak one protocol; consider open-sourcing the simulator + SDK; community is the moat.
- **Quantopian** (shut down 2020): crowdsourced backtests were overfit — their own 2016 study found backtest metrics had "little value" out-of-sample; monetizing participants' alpha failed. Lessons: score **forward runs**, not fixed backtests; the business is the venue (validation, benchmarking, infra), never harvesting participants' strategies.
- **Numerai**: era-wise scoring, obfuscated data, staking, originality bonuses vs the meta-model. Lesson: forward/live scoring windows as the official metric; obfuscation alone only delays leakage — rotate.
- **Modo Energy**: leaderboards of **real assets** ($/kW-yr, revenue-stream breakdown, top-bottom "perfect foresight" spread benchmark) became the industry reference (>90% of GB BESS owners use it; optimizers like Tesla Autobidder market their Modo rank). Lessons: express scores as **% of perfect-foresight spread captured** and **$/kW-month vs benchmark** — the language real traders already speak; a credible benchmark is a press-worthy flywheel.
- **Gridcog**: commercial proof that "simulate *my* assets, *my* market rules, *my* commercial position" is a product traders/developers pay for. EFlux adds live paper trading, agents, and competition on top.
- **Enertel (backtesting best practices)**: point-in-time data vintaging (decisions may only see data available at decision time), knowledge cutoffs, rolling-window validation across regimes, metrics tied to P&L (CRPS/calibration for probabilistic forecasts; top-bottom-spread capture for batteries). These are adopted as evaluation invariants (§6, §8).

## 3. Product structure

Routes kept: `/market /participants /leaderboard /arena /benchmarks /forecasts /vpps /login`. Added: `/competitions`, `/competitions/:slug`, `/competitions/:slug/submit`, `/submissions/:id`, `/vpps/new`, `/vpps/:id`, `/prove-out`, `/developer`.

- Desktop nav: `Live Market / Leaderboard / Arena / Compete / Prove-out`; Participants, Benchmarks, Forecasts, Developer under **Explore**. Mobile: header drawer (replaces the clipping `overflow-x-auto` row).
- New-user journey (competition): land → email sign-in → managed agent **or** container bot → sandbox validation → official submission → rankings + replays.
- New-user journey (trader): land → email sign-in → **describe endowment** → historical prove-out report → live paper trading → (optional, opt-in) prove-out leaderboard.
- `/vpps` (1,492-line `MyVPPs.tsx`) splits into: deployment wizard, Agent Cockpit, manual trading, Developer Console.
- Managed presets: Solar Trader, Battery Arbitrageur, Demand Optimizer; advanced params collapsed.
- Cockpit shows: runtime status, first trade, PnL, vs-benchmark, strategy log, risk rejections, model usage, submissions, replays.

## 4. Prove-out tier (real traders) — Phase A

The sandbox market, benchmark fleet, forecasting service, imbalance settlement, and endowment primitives already exist; this tier is packaging, not new physics.

- **Endowment wizard:** battery (MW / MWh / round-trip η / optional cycle cost), solar (MW + profile), load profile, starting cash. Maps onto existing VPP endowment config.
- **Historical prove-out:** replay the user's endowment over selected historical CAISO windows with **point-in-time discipline** — the strategy sees only data (incl. forecasts) that existed at each decision time; forecasts are replayed from stored vintages (`forecast_outcomes`, §8), never regenerated with hindsight.
- **Live paper trading:** same endowment in the live sandbox market against the benchmark + community fleet.
- **Report:** PnL, **$/kW-month**, **% of perfect-foresight top-bottom spread captured**, max drawdown, imbalance penalties, risk rejections, and per-stream breakdown — vs three anchors: perfect foresight, benchmark fleet (AA/ZIP/GD/PPO/managed-LLM), and do-nothing.
- **Privacy:** prove-out results are **private by default**; leaderboard participation is opt-in and pseudonymous. Traders will not touch a tool that outs their book.
- Not in v1: real-money execution, brokerage/SC integration, non-CAISO markets (structure copy so both can come later).

## 5. Design system (unchanged essentials, dials removed)

- Keep: EFlux logo, animated `GridCanvas`, dark/light, cyan accent. Body font: self-hosted **Geist Sans** (note: Inter was declared but never loaded — this also fixes a real inconsistency); **Geist Mono** for numbers/timestamps/code. Wordmark single-color.
- Cyan = sole interactive accent; green/amber/red = status only; resource colors only in charts/labels. Radii 12/8/pill. Glass only on landing overlay + true overlays; solid surfaces elsewhere. Reduced gradients/glows.
- Landing: hero fits one viewport; one real market-status overlay labeled `real / cached / synthetic`; no fake dashboards; no repeated glass-card walls.
- Market page: 4 first-viewport metrics (latest price, spread, supply-demand balance, active agents); merit order + price trend primary; order book/trades right rail; chat → "Agent Activity" tab.
- Participants: `archetype` + `resources[]` split; search + filters; table on desktop, cards on mobile.
- Leaderboard tabs: `Live / Managed / Container Standard / Container Model / Prove-out (opt-in)`, each showing rules version, sample size, observation window, cost, score breakdown.
- Arena: win/loss comparisons only after minimum trades + observation window; explicit data-collection state before that. Monospace numerics; no `0.00 / -0.00 / four-decimal` noise; forecast lines carry provenance badges (§8).
- Motion: 180–240ms transform/opacity; live node pulses OK; no scroll hijacking/marquees; `prefers-reduced-motion` + reduced-transparency fallbacks everywhere.

## 6. Competition tracks & rules v1.1

Three independently-ranked tracks: **Managed**, **Container Standard** (no egress; local weights allowed), **Container Model** (models only via the platform Model Proxy).

**Cadence & deadlines**
- Standard: one action window per **5 minutes** of sim time (288/seed-day), 500ms deadline.
- Model: one strategic window per 60 sim-minutes (24/seed-day), 15s deadline, request/token/cost caps.
- Per-seed wall cap is **derived, not fixed**: `30s handshake + ticks × (deadline + 200ms overhead) + 20% margin` (≈ 4.5 min Standard, ≈ 8.5 min Model). A bot that uses its full legal deadline budget cannot bust the cap.

**Seeds & anti-overfitting (Quantopian/Numerai lessons)**
- Per round: 3 **practice seeds** (public, replayable freely, local harness) + 5 **hidden seeds** (rotated every round) + 2 **holdout seeds** (final official ranking only).
- Submission cooldown: 2 official evaluations per track per day.
- Hidden/holdout replays and logs are embargoed until the round closes; during a round, hidden-seed runs return aggregate metrics only.
- Missing action = NOOP; >10% deadline misses or early exit = participant failure for that seed. Infra failures auto-retry ×2. One failed seed → benchmark lower bound for that seed (defined per seed as the benchmark fleet's 10th percentile); >1 failed seed → unranked.
- Score v1.1: per-seed normalization against the benchmark fleet, aggregated by **median** across seeds (a floored outlier seed can't be averaged away). PnL, drawdown, risk rejections, deadline misses, trade count, and model cost are recorded alongside.

## 7. Container platform (architecture unchanged, parameters fixed)

- Private OCI registry; per-submission repo + short-lived push token; `linux/amd64`; entrypoint required; runs as UID 65532; finalize resolves to an **immutable digest** (evaluations pull by digest only). Trivy vuln/secret/config scan + SBOM on finalize. Compressed size cap: **4GB Standard / 1GB Model**. Registry GC: keep the last 3 finalized digests per participant + all officially-ranked digests per season.
- Runtime: one K8s Job/Pod per seed — participant container + evaluator sidecar (fixed-version simulator, built-in opponents, RiskGate, MatchingEngine, gateway). gVisor RuntimeClass, non-root, read-only rootfs, no privilege escalation, all caps dropped, default seccomp, no host mounts, no SA token, default-deny egress (evaluator + Model Proxy only). 1 CPU / 1GiB / 128 PIDs / 64MiB tmp / 10MiB logs per container.
- **Sidecar hardening (new):** containers in a pod share localhost — every evaluator endpoint authenticates the per-run token, and an acceptance test asserts *no unauthenticated listener* exists in the sidecar (metrics/debug included).
- Evaluation Protocol v1 (step-based, separate from the live Agent Protocol): `POST /evaluation/ready`, `GET /evaluation/next?after_tick_id=`, `POST /evaluation/actions` (idempotency key); `ObservationEnvelope` = run id, tick id, sim time, deadline, market snapshot, VPP state, open orders, resource budget — **point-in-time only** (Enertel invariant). Orders pass the same RiskGate/MatchingEngine; no bypass.
- SDK: `EFluxEvaluationClient.from_env()` + `run(policy)` (handshake, long-poll, retries, deadlines, shutdown handled). Runner injects `EFLUX_EVALUATION_URL/TOKEN/RUN_ID/TRACK` (+ `EFLUX_MODEL_PROXY_URL` on Model track). Official Python starter + Dockerfile + CI push example.
- Model Proxy: OpenAI-compatible; allow-listed models; per-run request/token/cost budgets enforced server-side; per-account monthly quota; logs model/version/tokens/cost/response summary; guidance/action stream retained for replay.

## 8. Forecast-quality workstream (F5) — shipped 2026-07-10 + forward gates

Diagnosed root causes (all confirmed in code, most reproduced deterministically offline):
1. Chart semantics: the horizon overlay plotted *historical* forecasts shifted to target time and joined across gaps and into the future — the post-"now" line was never the current outlook. **Fixed:** current-fan forward lines from the latest bundle; historical overlay dimmed "(as issued)"; nulls break lines.
2. DAM anchor rot: partial DAM curves accepted (55/72h), future targets as-of-filled up to 26h stale, absent curve → hard $50. **Fixed:** coverage required through target, ≤2h as-of tolerance, partial caches expire, explicit `degraded_persistence_shape` fallback with provenance.
3. Unbounded global residual (−$33.14 from 32 updates → $0.42 forecasts; ±$10,000 bounds). **Fixed:** residual clipped (±$20 default) + EWMA clamp + reset on anchor-source change; final forecasts bounded to a configurable plausible band.
4. No horizon blending (1h/12h = raw DAM + residual; realized price ignored; 5m lost to persistence). **Fixed:** `w(h)=min(h/6h, 0.85)` persistence↔anchor blend, residual decay `exp(-h/4h)`, 5m = pure persistence; calibrated offline on cached windows.
5. Warm-start & starvation: sparse state (336 pts / 337h hole) accepted while full 720-pt caches existed; P2P mode never polled CAISO (price model starved since Jul-05); local-vs-UTC hour-feature mismatch. **Fixed:** densest-contiguous selection with min-points + max-gap, state version bump (`online-rls-v2`) invalidates stale state, config-gated CAISO polling in P2P mode, market-timezone canonicalization.
6. P2P anchored on CAISO DAM (+$19 structural bias vs P2P clearing). **Fixed (user decision 2026-07-10):** own-market rolling hour-of-day clearing profile as anchor; explicit `p2p_cold_start` fallback; realprice keeps DAM.
7. No persisted outcomes (12h skill was unmeasurable). **Fixed:** `forecast_outcomes` table (migration 0004): origin, target, horizon, anchor, residual, predicted, realized backfill; `/forecasts/history` serves from DB when hot cache is cold.

Forward gates (Forecast Hub becomes trustworthy or says it isn't):
- Publish per-horizon **skill scores** (MAE/bias vs persistence and vs anchor-only) computed from `forecast_outcomes`, shown on `/forecasts`; degraded/cold-start provenance rendered as badges.
- Alert when rolling 24h 1h-MAE exceeds persistence (a forecaster worse than "no forecaster" must page someone, not decorate a chart).
- CI gates: 5m MAE ≤ 1.05× persistence; 1h < persistence; all outputs within bounds; near-now continuity (|5m forecast − last price| bounded) — enforced on the offline replay harness.
- Follow-up (Phase A): probabilistic bands (CRPS/percentile calibration) so trading strategies can size risk, per Enertel practice.

## 9. Backend, reliability, governance

- **Entities:** Competition, CompetitionRuleSet, Submission, ContainerArtifact, EvaluationRun, EvaluationSeedRun, EvaluationMetric, ModelUsage, AuditEvent, **User.role** (`user | admin`; PPO renew + competition admin = admin-only), ProveOutRun.
- **APIs:** the draft's competition/submission/evaluation set, plus `GET /vpps/presets`, `GET /auth/me`, `POST /auth/logout`, archetype/resources on summaries, session/sim-time/provenance on market snapshots.
- **Auth:** browser sessions → Secure/HttpOnly/SameSite cookies; API keys + evaluation tokens stay Bearer. Magic link via a real provider (default assumption: Resend; SPF/DKIM configured); production never returns the token; dev echo stays behind `env=dev`.
- **Data:** Postgres becomes the default in deploy (SQLite stays for local dev; alembic already in place — this is promotion, not migration). Redis required in prod (rate limits, short-lived state, job notify; the Streams bus exists). S3-compatible artifact store for logs/replays/charts. Evaluator jobs claimed transactionally from Postgres by a dedicated worker.
- **Startup:** API starts on cached/synthetic data; CAISO/weather/forecast/LLM warm asynchronously; `/health/live`, `/health/ready`, per-source status; runner heartbeats. (Matches the 07-09 zero-forecast incident hardening.)
- **Observability:** request IDs, structured logs, error tracking, container metrics, queue latency, failure reasons, model-cost alerts.
- **CI (two-tier, from zero):**
  - *PR gate (< 3 min):* ruff (fix the remaining 17, then zero-tolerance), type check, offline pytest (network-off by default; CAISO/weather/LLM tests opt-in), frontend lint/test/build, OpenAPI diff, SDK contract tests, forecast skill gates.
  - *Async pipeline (nightly/release, non-blocking):* Trivy + SBOM, evaluator protocol integration, K8s+gVisor staging canary (push→finalize→scan→run→score→replay→cleanup).
- **Frontend perf:** route-level code splitting + lazy ECharts; initial JS < 250KB gzip; LCP < 2.5s, INP < 200ms, CLS < 0.1.
- **Types:** TS + Python protocol models generated from OpenAPI.

## 10. Phasing & estimates

| Phase | Scope | Traditional | Solo + agents |
|-------|-------|------------|----------------|
| **A — Product core + Prove-out** | Cookie auth + real email + roles; competition entities/APIs; managed track end-to-end; `/vpps` split; nav/landing/market IA; leaderboard tabs + thresholds; prove-out tier (wizard, historical replay, report); forecast forward-gates; perf split; Postgres promotion; PR-gate CI | 9–12 wk | **3–5 wk** |
| **B — Container Standard** | Registry + tokens + scan/SBOM; evaluation protocol + SDK client; evaluator sidecar; K8s+gVisor runner + worker; artifact store; replays; async CI pipeline + canary | 8–11 wk | **4–7 wk** (wall-clock-bound by staging infra) |
| **C — Container Model** | Model Proxy + budgets/quotas; Model-track rules; cost telemetry + alerts | 3–4 wk | **1–2 wk** |
| Total | | 20–27 ew | **~2–3.5 months** |

Acceptance per phase: all Phase-N criteria green before N+1 ships publicly (B/C development may start earlier behind flags). Test policy: **no regressions, and every new endpoint/entity/protocol path lands with tests** — suite count grows monotonically (465 today).

## 11. Acceptance criteria (v1.1)

**Product:** first official submission (managed) ≤ 5 min from landing, given email delivery ≤ 60s (dev/demo bypass documented); first prove-out report ≤ 10 min from landing; no clipped nav or horizontal scroll at 390/768/1280/1440; hero fits first viewport; dark+light pass WCAG 2.2 AA; full keyboard paths for login, deploy, submit, filter, evaluate; provenance labels never show synthetic data as real.
**Container evaluation:** no privileged containers/host mounts/SA tokens/egress; no cross-submission leakage (network, fs, tokens, logs, results); tag updates never change a finalized digest; same rules+seed+action stream ⇒ identical result; sidecar exposes no unauthenticated listener; deadline/NOOP/risk-rejection/crash/OOM/timeout/retry paths covered by automated tests; Standard vs Model resource + budget isolation enforced; staging canary completes the full lifecycle.
**Forecasts:** skill scores published; CI gates hold on the replay harness; degraded states visibly badged; worse-than-persistence alert wired.
**Engineering:** PR gate < 3 min with no undeclared network; ruff zero after the 17 are fixed; API starts (degraded) with CAISO/weather/LLM/registry down.

## 12. Assumptions & open questions

- CAISO-only v1; market abstraction kept clean for ERCOT/EU later. English UI, copy centralized for future zh/en.
- Simulation only — prove-out informs real trading but places no real bids; no participant API keys on Model track; public live market stays a sandbox; official results come only from isolated evaluations.
- Brand, Tailwind v4, Lucide, existing routes retained.
- Pricing: free during v1; candidate models later — freemium tiers (QuantConnect pattern) and/or paid prove-out reports / benchmark subscriptions (Modo pattern). Decide before Phase B ships.
- Open: email provider — **deferred (user, 2026-07-10)**: dev token echo stays until a provider is chosen; must be decided before public launch. Prove-out leaderboard pseudonymity rules; whether Standard track allows GPU (v1: no).

## 13. Research sources

PowerTAC (powertac.org); QuantConnect/LEAN (quantconnect.com, lean.io); Quantopian post-mortems (QuantRocket "3 Takeaways", Michael Harris, Robot Wealth); Numerai docs (docs.numer.ai — scoring/staking); Modo Energy (leaderboard, GB/ERCOT benchmark methodology, optimizer rankings); Gridcog (gridcog.com — simulation/planning tour); Enertel AI ("Best practices for backtesting power trading strategies"); Tesla Autobidder (tesla.com/support/energy — Modo #1 ERCOT 2022/23 ranking claim).
