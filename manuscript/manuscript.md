# EFlux Manuscript Writing Plan

## 0. Core Positioning

Working title:

**EFlux: An Open, Reproducible, Extensible, and Auditable Agent-Based Platform for AI-Enabled Electricity Market Research**

Recommended paper positioning:

EFlux should be framed primarily as an **open electricity-market research platform** and secondarily as an **auditable agent framework** for studying AI-assisted VPP trading. The paper should not be written as only "our PPO/LLM agent earns more profit." The stronger and more defensible story is:

1. Electricity markets are becoming a critical AI research domain because AI is both a new source of electricity demand and a tool for operating increasingly complex distributed energy systems.
2. EFlux provides an open platform and a reproducible, extensible, auditable agent framework for studying this domain.
3. Experiments validate the robustness of the framework under different market mechanisms, data regimes, agent classes, and failure modes. Numerical results will be inserted after the full experimental run.

The paper should keep the contribution order exactly in this logic: **importance of electricity market research -> platform and agent framework -> robustness validation**.

## 1. Contribution Structure

### Contribution 1: Why electricity market research matters, especially with AI

This contribution should motivate the problem before presenting the system.

Key argument:

Modern power systems are facing two coupled pressures. First, electrification, distributed energy resources, storage, electric vehicles, and renewable intermittency make electricity markets more dynamic and more decentralized. Second, AI changes both sides of the equation: AI workloads and data centers increase electricity demand, while AI methods such as reinforcement learning, LLM-based agents, forecasting, and autonomous bidding are increasingly proposed for market operation and energy management. This creates a need for trustworthy simulation environments where AI agents can be developed, compared, audited, and stress-tested before any deployment-relevant use.

Points to cover:

- Electricity is a critical infrastructure market; bad decisions affect cost, reliability, emissions, and fairness.
- VPPs and DERs make market behavior more agentic: many small assets can now act as coordinated market participants.
- AI increases electricity demand through data-center loads and changes load profiles; this makes AI part of the electricity-market problem, not only a solution.
- AI also creates new tools for market participation: RL bidding, LLM strategy generation, autonomous VPP operators, and adaptive price-taking or P2P agents.
- Existing research needs environments that support controlled experimentation, reproducibility, heterogeneous agents, market mechanism comparisons, and failure auditing.

Suggested wording style:

Do not overclaim that EFlux solves grid operation. Say it provides a controlled research substrate for studying VPP market behavior and AI-assisted bidding under explicit safety boundaries.

### Contribution 2: Open platform plus reproducible, extensible, auditable agent framework

This is the technical core of the paper.

Platform claims supported by current code:

- Two market modes:
  - `p2p`: peer-to-peer continuous double auction with price-time priority.
  - `realprice`: price-taking against a CAISO-derived real-time price signal.
- Historical backtesting:
  - strict headless runner;
  - configurable window, tick size, market mode, scenario, LLM cadence;
  - artifacts including manifest, participant metrics, group metrics, market time series, and SVG charts.
- Data sources:
  - CAISO OASIS LMP for market-price signal and trailing-month reference price;
  - Open-Meteo weather for PV and wind;
  - synthetic fallback paths that are explicitly labeled.
- Reproducibility:
  - scenario YAML files;
  - fixed seeds;
  - schema-validated `AgentSpec`;
  - deterministic benchmark runner;
  - saved manifests for backtests.
- Extensibility:
  - shared internal/external participant schema;
  - REST and WebSocket surfaces;
  - pluggable agent classes;
  - per-market PPO warm-start checkpoints;
  - new classical baselines: ZI, Truthful, ZIP, GD, AA.
- Auditability:
  - order, trade, tick, reflection, rejection, and backtest artifact logs;
  - LLM guidance is structured, bounded, and inspectable;
  - RiskGate provides a common validation boundary.

Agent framework claims supported by current code:

- `AgentContext -> BaseAgent.decide(ctx) -> OrderIntent[] -> RiskGate -> MatchingEngine`.
- `TruthfulValuationOracle` separates economic valuation from trading strategy.
- Structured strategy actions prevent learned policies from emitting arbitrary raw order books.
- PPO acts over a small safe primitive set: `NOOP`, `LIQUIDATE_SURPLUS`, `COVER_DEFICIT`, `BATTERY_ARBITRAGE`.
- LLM strategist is slow and advisory: preferred/avoid modes, risk budget, SOC target, meta-control.
- LLM does not directly submit orders; guidance is parsed, clamped, cached, and read off the tick path.
- Hybrid agents can spawn PPO mirrors, making LLM contribution measurable by A/B attribution.

Important caution:

The current PPO action set is intentionally compact. The paper can describe the broader strategy-language design, but experiments should clearly state which primitives are active in the reported PPO path.

### Contribution 3: Robustness validation

This contribution should be written as an experimental claim, with final numbers inserted after runs complete.

Robustness dimensions:

- Market-mechanism robustness: compare P2P CDA and real-price price-taking.
- Data-regime robustness: compare synthetic fallback, CAISO historical price, Open-Meteo weather, and degraded data cases.
- Agent-population robustness: evaluate consumer/provider/VPP taxonomy with Truthful, ZIP, GD, AA, PPO, and Hybrid agents.
- Learning robustness: compare BC warm-start, online PPO, PPO mirror, and LLM-guided Hybrid.
- Safety robustness: report RiskGate rejections, invalid action handling, SOC behavior, unresolved imbalance, and fallback behavior.
- LLM robustness: strict LLM backtest validation, retry behavior, failure accounting, skipped calls, and separation from the critical tick path.

Results section should use placeholders until experiments finish. Example:

`[RESULT TO FILL: Hybrid agents improve risk-adjusted PnL by X% over PPO mirrors in P2P mode while maintaining SOC violation below Y%.]`

## 2. Recommended Manuscript Outline

### Abstract

One paragraph, four moves:

1. Motivate AI-electricity coupling and VPP market complexity.
2. Present EFlux as an open agent-based electricity-market platform.
3. Summarize the auditable agent framework: valuation oracle, structured actions, PPO executor, LLM strategist, RiskGate.
4. State that experiments across P2P, real-price, historical backtests, baselines, and failure modes validate robustness.

Do not include exact results until the experiment table is complete.

### 1. Introduction

Goal:

Make electricity market research feel necessary before introducing EFlux.

Suggested flow:

1. The electricity system is moving from centralized dispatch toward distributed, renewable, storage-backed, and flexible-load markets.
2. AI is now tightly coupled with electricity:
   - AI infrastructure creates large, flexible or semi-flexible loads;
   - AI methods are used for forecasting, dispatch, bidding, and autonomous energy management;
   - AI agents can create market efficiency gains but also introduce safety and audit risks.
3. VPPs are a natural setting for studying this coupling because they aggregate PV, wind, storage, EVs, flexible loads, and dispatchable resources.
4. Existing environments often lack one or more of: openness, reproducibility, extensibility, heterogeneous baselines, real-price/weather integration, and auditability.
5. EFlux addresses this with an open platform plus a constrained, inspectable agent framework.
6. List the three contributions in the exact requested order.

Contribution paragraph:

- We motivate electricity markets as a critical AI research domain under growing AI demand and AI-enabled autonomous market participation.
- We introduce EFlux, an open VPP electricity-market platform with reproducible scenarios, extensible participants, and auditable agent execution.
- We evaluate the framework's robustness through P2P and real-price markets, historical backtests, heterogeneous agent baselines, and explicit safety/failure analyses.

### 2. Background and Related Work

Recommended subsections:

#### 2.1 Electricity Markets, VPPs, and DER Coordination

Cover:

- Continuous double auctions and price-time priority;
- VPP aggregation;
- distributed renewable generation;
- batteries and flexible loads;
- market clearing, price-taking, and P2P trading.

#### 2.2 AI for Power Systems and Power for AI

This subsection directly answers the user's first requirement.

Cover both directions:

- **AI for power**: forecasting, bidding, dispatch, demand response, RL, LLM-assisted strategy design.
- **Power for AI**: AI data centers and compute clusters create new load profiles, grid stress, and opportunities for flexible demand response.
- Why simulation is needed: real grids and markets are too risky, expensive, and hard to randomize for early-stage AI-agent experiments.

#### 2.3 Agent-Based Market Simulation and Autonomous Bidding

Discuss:

- ZI, ZIP, GD, AA, Truthful, RL, and LLM-based bidding agents.
- The need to compare AI agents against classical market-agent baselines, not only naive baselines.
- The need for audit logs and risk boundaries when agents are learned or LLM-guided.

#### 2.4 Gap

End this section with the gap:

There is a shortage of open platforms that combine realistic VPP assets, multiple market mechanisms, real data integration, classical and AI baselines, reproducibility, and agent-level auditability.

### 3. EFlux Platform

This section supports Contribution 2 from the platform side.

#### 3.1 System Overview

Describe:

- Backend simulator loop;
- matching engine;
- DER models;
- agent roster;
- API/WebSocket;
- backtest runner;
- frontend only as visualization, not core contribution.

Recommended Figure 1:

Architecture diagram with data sources, simulator, market engine, agents, RiskGate, API, and backtest artifacts.

#### 3.2 Market Modes

Explain the two current modes:

- **P2P CDA market**:
  - peer-to-peer book;
  - price-time priority;
  - resting-order price clearing;
  - CAISO used as reference, not a valuation cap.
- **Real-price market**:
  - agents are price-takers;
  - orders settle against grid import/export prices around CAISO LMP;
  - no peer price impact;
  - suitable for timing and storage-arbitrage tests.

This is a major update from the earlier plan and should be prominent.

#### 3.3 DER and Participant Taxonomy

Describe participant classes:

- Consumers: EV, industrial, commercial, residential.
- Providers: PV, wind, gas.
- VPPs: mixed PV/wind/battery/load portfolios.

Mention that `p2p.yaml` currently uses 40 declared participants and 44 live participants after LLM mirrors; `realprice.yaml` uses 29 declared and 33 live participants. Verify these counts before final submission if rosters change.

#### 3.4 Real and Synthetic Data

Describe:

- CAISO OASIS LMP;
- trailing-month CAISO reference price;
- Open-Meteo weather;
- pvlib-backed PV where available;
- explicit fallback labeling.

Important framing:

Fallbacks are not hidden. The platform labels synthetic and degraded paths in data-source status, manifests, and backtest outputs. This supports auditability.

#### 3.5 Reproducibility and Backtesting

Describe:

- YAML scenarios;
- `AgentSpec` validation;
- fixed seeds;
- headless historical backtests;
- manifest and CSV/SVG artifacts;
- strict LLM mode with retries and failure accounting.

Recommended Table 1:

Platform capabilities:

| Capability | EFlux mechanism |
|---|---|
| Reproducible scenarios | YAML + schema + fixed seeds |
| Market mechanisms | P2P CDA and real-price price-taking |
| Real data | CAISO LMP, Open-Meteo weather |
| Extensible agents | `BaseAgent`, `AgentSpec`, executor spec |
| Auditability | logs, manifests, RiskGate, LLM reflection trail |
| Historical evaluation | strict backtest runner and artifacts |

### 4. Auditable Agent Framework

This section supports Contribution 2 from the agent side.

#### 4.1 Unified Agent Execution Contract

Present the common pipeline:

`AgentContext -> decide(ctx) -> OrderIntent[] -> RiskGate -> MatchingEngine / Grid Settlement`

Explain that built-in, learned, hybrid, fallback, and external/UI-submitted orders go through a common validation path.

#### 4.2 Valuation as an Oracle, Not a Policy Bottleneck

Explain:

- `TruthfulValuationOracle` estimates fair buy/sell values, battery value, imbalance, SOC pressure, and dispatchable gas supply.
- It gives economic grounding to baselines and learned agents.
- It avoids forcing PPO/LLM to directly infer basic energy economics from sparse market outcomes.

#### 4.3 Structured Strategy Language

Explain:

- Agents choose primitives and bounded parameters rather than arbitrary raw order books.
- Compiler translates strategy actions into orders.
- This improves interpretability, trainability, and auditability.

Mention active PPO primitives:

- `NOOP`
- `LIQUIDATE_SURPLUS`
- `COVER_DEFICIT`
- `BATTERY_ARBITRAGE`

Mention broader framework primitives only as supported infrastructure or future extension if not experimentally active.

#### 4.4 Baseline Agents

Current baselines:

- ZI: random rational trader.
- Truthful: cost/value-based economic baseline.
- ZIP: adaptive margin learning.
- GD: belief-based acceptance-probability bidding.
- AA: adaptive aggressiveness around equilibrium estimate.
- Strategy/PPO: structured-action online learner.
- Hybrid: PPO plus LLM strategist.

The paper should emphasize that adding ZIP/GD/AA makes the evaluation stronger because PPO/LLM are compared to classical CDA trading agents, not only weak random baselines.

#### 4.5 PPO and Behavior-Cloning Warm Start

Explain:

- PPO operates over structured action vectors.
- Current training path uses behavior cloning from scripted strategy demonstrations as a warm start.
- With `--real-data`, training uses real CAISO price and Open-Meteo weather.
- Separate checkpoints are used for P2P and real-price market structures.
- Online PPO fine-tunes in simulation.

Do not overstate full MARL training unless later experiments add it.

#### 4.6 LLM Strategist and Auditability

Explain:

- LLM is a slow strategist, not an execution authority.
- It returns structured guidance: preferred modes, avoid modes, risk budget, SOC target, and meta-control.
- Guidance is parsed, clamped, cached, and logged.
- Calls are staggered and off the critical tick path in live simulation.
- Strict backtests can require live LLM responses and abort or retry on failures.

Recommended Figure 2:

Agent decision pipeline:

`Market/VPP state -> valuation oracle -> PPO executor -> strategy action -> compiler -> RiskGate -> market/grid`

With LLM strategist feeding soft guidance into PPO and meta-control, not orders.

### 5. Experimental Design

This section supports Contribution 3. It should be written before final results are available, with placeholders for values.

#### 5.1 Research Questions

RQ1: Can EFlux represent both peer-to-peer price discovery and real-price price-taking for heterogeneous VPP participants?

RQ2: Does the auditable agent framework support reproducible and extensible comparisons across classical, economic, learned, and LLM-guided agents?

RQ3: Is the framework robust under market-mode changes, real-data replay, LLM failures, data degradation, and safety constraints?

RQ4: Does LLM guidance improve or stabilize PPO behavior compared with strategist-less PPO mirrors?

#### 5.2 Experimental Axes

Market modes:

- P2P CDA.
- Real-price price-taking.

Data regimes:

- synthetic price/weather;
- CAISO historical LMP;
- Open-Meteo weather;
- fallback/degraded data.

Agent groups:

- ZI;
- Truthful;
- ZIP;
- GD;
- AA;
- PPO / Strategy;
- Hybrid LLM+PPO;
- PPO mirror.

Participant taxonomy:

- consumers;
- providers;
- VPPs.

#### 5.3 Metrics

Market-level:

- total load and renewable generation;
- P2P last price, best bid/ask, spread, mid price;
- CAISO LMP reference;
- trade volume;
- market depth;
- price volatility.

Agent-level:

- realized PnL;
- mark-to-market value;
- energy bought/sold;
- unresolved imbalance;
- final SOC and SOC violation time;
- battery throughput or degradation proxy;
- fill rate;
- risk rejections;
- trade count.

Robustness and audit metrics:

- LLM calls;
- LLM failures/retries/skips;
- strict backtest aborts or partial artifacts;
- synthetic fallback labeling;
- invalid actions rejected by RiskGate;
- runtime ticks/sec and scalability.

Important note on money units:

The code uses prices in $/MWh and quantities in kWh; internal cash is `price * kWh`, which is 1000x true USD. Use converted USD for user-facing tables if needed, and state the convention clearly.

#### 5.4 Main Experiments

Experiment A: Market-mode comparison

- Run matched scenarios in P2P and real-price modes.
- Compare market prices, agent PnL, SOC behavior, and imbalance.
- Expected claim: EFlux can express both endogenous peer price discovery and exogenous grid price-taking within one framework.

Experiment B: Historical backtest

- Run one-month historical backtests with CAISO LMP and weather.
- Use per-market rosters and checkpoints.
- Output manifest, participant metrics, group metrics, price/LMP charts, supply/demand charts.
- Insert results after completion.

Experiment C: Baseline comparison

- Compare Truthful, ZI, ZIP, GD, AA, PPO, and Hybrid agent families.
- Report by participant category: consumer, provider, VPP.
- Avoid reducing evaluation to raw PnL. Include imbalance, SOC, and risk metrics.

Experiment D: LLM contribution via PPO mirror

- For each Hybrid VPP, compare against its mirrored PPO-only twin.
- Same seed and portfolio, same checkpoint, no strategist.
- This isolates the marginal contribution of LLM guidance and meta-control.

Experiment E: Robustness and failure analysis

- LLM unavailable or malformed response.
- Data fallback from CAISO/weather.
- RiskGate invalid order rejection.
- Market shock or high-volatility window.
- Backtest partial artifact recovery after failure.

#### 5.5 Ablations

Recommended ablations:

- PPO with BC warm-start vs fresh online PPO.
- PPO trained on synthetic data vs real data.
- P2P-trained checkpoint in P2P vs real-price-trained checkpoint in real-price.
- Hybrid with LLM guidance vs PPO mirror.
- LLM guidance only vs LLM meta-control plus guidance, if implementation/results allow.
- RiskGate enabled vs stress-test invalid agents, not necessarily disabled in main experiments.

### 6. Results

Keep this section as a fill-in scaffold until experiments finish.

Suggested subsections:

#### 6.1 Market Dynamics and Reproducibility

To fill:

- Price/LMP and supply/demand plots.
- Reproducibility across fixed seeds.
- P2P vs real-price behavior.

Placeholder:

`[RESULT TO FILL: Across N fixed-seed runs, EFlux reproduces identical market trajectories under identical scenario and seed settings.]`

#### 6.2 Agent Performance Across Baselines

To fill:

- Leaderboard by agent class.
- Grouped consumer/provider/VPP results.
- PnL plus safety metrics.

Placeholder:

`[RESULT TO FILL: PPO/Hybrid performance relative to Truthful, ZIP, GD, and AA.]`

#### 6.3 Robustness Under Real Data and Market Shifts

To fill:

- Historical backtest results.
- Synthetic vs real-data comparison.
- Shock windows.

Placeholder:

`[RESULT TO FILL: Real-data-trained checkpoints reduce imbalance by X% in historical replay compared with synthetic-trained checkpoints.]`

#### 6.4 LLM Guidance and Mirror Attribution

To fill:

- Hybrid vs PPO mirror.
- LLM guidance frequency and failure stats.
- Primitive distribution changes.

Placeholder:

`[RESULT TO FILL: LLM-guided Hybrid agents improve risk-adjusted outcomes by X% over PPO mirrors while maintaining comparable RiskGate rejection rates.]`

#### 6.5 Audit and Failure Analysis

To fill:

- Risk rejection examples.
- LLM retry/failure behavior.
- Data-source fallback manifests.
- Partial artifact recovery.

Placeholder:

`[RESULT TO FILL: In strict LLM backtests, failed calls are retried up to K times and all failures are logged in the manifest/artifacts.]`

### 7. Discussion

Discussion points:

- Why electricity-market AI needs auditability rather than only stronger black-box policies.
- Why LLM is constrained to strategy guidance, not direct order placement.
- Why structured action spaces are a practical middle ground between Truthful-only policies and arbitrary raw order-book generation.
- Why P2P and real-price markets answer different research questions.
- How classical CDA baselines strengthen the experimental comparison.

Limitations:

- No transmission network or power-flow constraints yet.
- Distribution-level constraints and locational congestion are not modeled.
- Current PPO action set is compact.
- Strict LLM backtests can be expensive because live LLM calls dominate wall-clock time.
- Real data integration depends on CAISO/Open-Meteo availability and fallback behavior.
- Market participants are simulated VPPs, not deployment agents.

### 8. Conclusion

Conclusion should mirror the three contributions:

1. Electricity markets are an important AI research domain because AI changes demand and creates new autonomous decision-making tools for power systems.
2. EFlux provides an open, reproducible, extensible, and auditable platform and agent framework for VPP market research.
3. Experiments across market modes, historical data, baselines, LLM/PPO attribution, and failure cases validate framework robustness.

## 3. Figure and Table Plan

Figure 1: System architecture

- Data sources: CAISO, Open-Meteo.
- Simulator and DER layer.
- Market modes: P2P CDA and real-price grid settlement.
- Agent framework: baselines, PPO, Hybrid.
- RiskGate.
- APIs and backtest artifacts.

Figure 2: Agent pipeline

- AgentContext.
- TruthfulValuationOracle.
- PPO executor.
- LLM strategist.
- StrategyAction and compiler.
- RiskGate.
- MatchingEngine or grid settlement.

Figure 3: Market dynamics

- P2P price, CAISO LMP, supply/demand, SOC over time.

Figure 4: Robustness or failure audit

- RiskGate rejection distribution, LLM failure/retry counts, or data-source status.

Table 1: Platform capability comparison

- Open;
- reproducible;
- real data;
- multiple market modes;
- classical baselines;
- RL/LLM support;
- audit logs;
- historical backtest.

Table 2: Scenario taxonomy

- P2P participants and real-price participants.
- Consumers/providers/VPPs.
- Agent types.

Table 3: Main performance results

- Agent class;
- PnL;
- mark-to-market;
- energy traded;
- imbalance;
- SOC;
- risk rejection.

Table 4: Robustness results

- Market mode;
- data regime;
- LLM status;
- RiskGate events;
- runtime.

Table 5: Ablation results

- BC warm-start;
- real-data training;
- LLM guidance;
- meta-control;
- PPO mirror.

## 4. Writing Rules for the Manuscript

Use these rules while drafting:

- Keep the first contribution as motivation, not implementation.
- Do not make the abstract sound like only an LLM-agent paper.
- Use "AI-enabled electricity market research" or "AI-assisted market agents" rather than implying operational grid deployment.
- Always pair profit metrics with safety and physical metrics.
- When discussing LLMs, emphasize constrained structured guidance and audit logs.
- When discussing PPO, emphasize structured actions and train/serve parity.
- When discussing real data, distinguish live/forecast data from historical replay data.
- Mark any missing numerical result with `[RESULT TO FILL: ...]`.
- Verify scenario counts and agent rosters immediately before final paper submission.

## 5. Current Code Facts to Recheck Before Submission

These are true based on the current repository scan and should be rechecked if the code changes:

- `scenarios/p2p.yaml`: 40 declared participants, 44 live participants after four PPO mirrors.
- `scenarios/realprice.yaml`: 29 declared participants, 33 live participants after four PPO mirrors.
- Agent kinds include `zi`, `truthful`, `zip`, `gd`, `aa`, `strategy`, `hybrid`, `reflective`, and `gas`.
- Executor kinds currently include `scripted` and `ppo_online`.
- PPO active primitive set is `NOOP`, `LIQUIDATE_SURPLUS`, `COVER_DEFICIT`, `BATTERY_ARBITRAGE`.
- Backtest defaults are one month, 1-second ticks, hourly strict live LLM cadence.
- Historical backtests write manifest, participant metrics, group metrics, time series, and SVG charts.
- `market_mode` supports `p2p` and `realprice`.
- CAISO reference price can be fixed to a trailing-month mean via `price_ref_source="caiso"`.

