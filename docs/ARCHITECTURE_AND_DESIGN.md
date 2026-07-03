# EFlux Project and Agent Architecture Context

**Originally drafted:** 2026-06-22 · **Last reviewed:** 2026-06-28  
**Purpose:** Captures the high-level design intent behind EFlux — the architectural vision and the agreed relationship between the LLM strategist, PPO policy, and Truthful valuation layers. This is the "why"; refer to the code and to [AGENT_SPEC.md](AGENT_SPEC.md) for current implementation detail.

**See also:** [EXTERNAL_PARTICIPATION.md](EXTERNAL_PARTICIPATION.md) — external-user onboarding: participation modes, policies, and the leaderboard (builds on §3 and §9 below).

> Before implementing changes, verify these notes against the current repository state. They record the architectural understanding and decisions reached in discussion, not an immutable description of the codebase.

## 1. Project Overview

EFlux is an agent-based virtual power plant (VPP) electricity-trading simulation platform. Multiple heterogeneous VPPs trade energy through a continuous double-auction market.

The simulated resources include:

- Solar PV
- Wind generation
- Batteries
- Flexible loads
- Gas generators

The primary system flow is:

```text
Simulation tick
    -> update VPP and DER state
    -> construct AgentContext
    -> agent decision
    -> OrderIntent
    -> matching engine
    -> order/trade events
    -> API, WebSocket, event bus, and UI
```

The system is best understood as a real-time market simulator rather than a conventional CRUD application.

### Main technical structure

```text
React + Vite frontend
        |
        | REST / WebSocket
        v
FastAPI backend
  |- API routers
  |- simulator loop
  |- continuous double-auction matching engine
  |- built-in VPP agents
  |- LLM reflection
  `- optional event-bus integrations

Database:
  users and VPP definitions

In-memory simulation state:
  orders, trades, PnL, market state, and most runtime state
```

### Core code path

The most important path to preserve and understand is:

```text
Simulator tick
    -> AgentContext
    -> BaseAgent.decide(ctx)
    -> OrderIntent[]
    -> MatchingEngine.submit()
    -> TradeEvent / OrderEvent
```

The matching engine uses a continuous double auction with price-time priority. Resting-order price is used as the execution price, and self-trading is prevented.

## 2. Existing Agent Model

All internal agents share the conceptual interface:

```python
decide(ctx: AgentContext) -> list[OrderIntent]
```

Important existing agent categories include:

- `ZIAgent`: randomized bidding within a rational range.
- `TruthfulAgent`: an economics-based policy using energy imbalance, marginal value, battery efficiency, and SOC-related logic.
- `GasGeneratorAgent`: dispatchable supply priced around generation cost.
- `ReflectiveAgent`: an LLM-assisted decorator around a baseline agent, currently centered on bounded hints such as price and quantity adjustments.
- `PPOAgent`: an early reinforcement-learning path with a training environment, checkpointing, and live inference wrapper.

### Current limitation

The original reflective architecture is safe but narrow:

```text
Truthful order
    -> LLM/PPO adjusts a few parameters
    -> final order
```

If LLM and PPO can only adjust fields such as:

```text
price_adjust
qty_scale
risk_appetite
```

then Truthful remains the true policy backbone. The learned components cannot discover meaningfully different trading behavior.

This is the central action-space bottleneck identified in the discussion.

## 3. External and Distributed Agent Architecture

External LLM agents should not be integrated by making remote model calls directly inside the simulator's synchronous tick path.

The preferred first-stage model is:

```text
External Agent
    |- subscribes to market events
    |- reads market and VPP state
    |- maintains its own memory and strategy
    `- submits validated order batches
```

This avoids allowing slow or unavailable remote agents to block the market loop.

### Canonical architecture

```text
External LLM Agent
        |
        | MCP / REST SDK / WebSocket / Redis Streams
        v
EFlux Agent Gateway
        |
        | EFlux Agent Protocol
        v
Validation and Risk Gate
        |
        v
Simulator + Matching Engine
```

The important distinction is:

```text
MCP, REST, WebSocket, and Redis are adapters.
The Agent Gateway is the trust and validation boundary.
The EFlux Agent Protocol is the canonical trading contract.
The Matching Engine accepts only validated intents.
```

### MCP conclusion

MCP is a useful interface for LLM hosts, but it must not become the market's only protocol or internal semantic model.

An EFlux MCP server may expose:

```text
Resources:
  market snapshot
  order book
  VPP state
  VPP performance

Tools:
  get_market_snapshot
  get_vpp_state
  get_open_orders
  submit_orders_batch
  cancel_orders
  get_recent_trades
```

These tools should call the same Agent Gateway and canonical API used by every other adapter.

### Required protocol semantics

The canonical protocol must represent concerns that a generic tool call does not solve by itself:

```text
protocol_version
agent_id
vpp_id and ownership
tick_id
idempotency_key
deadline
order TTL
maximum orders per tick
price and quantity limits
risk limits
late-response policy
fallback policy
audit information
replay cursor
```

### Preferred implementation order

1. Define EFlux Agent Protocol v1.
2. Add VPP state, open-order, batch-order, and cancellation APIs.
3. Build a Python SDK.
4. Implement an MCP adapter over the SDK or Agent Gateway.
5. Add an external-agent example.
6. Add platform-hosted `RemoteAgent` support only after the asynchronous, timeout, fallback, and late-response semantics are established.

## 4. Revised Agent Action Model

The agreed direction is to stop treating a single `OrderIntent` as the complete language of agent behavior.

The architecture should evolve through:

```text
OrderIntent policy
    -> OrderProgram policy
    -> structured Strategy DSL policy
```

### Strategy action

The learned policy should select a trading primitive and its parameters:

```python
class StrategyAction:
    mode: StrategyMode
    aggressiveness: float
    qty_fraction: float
    price_offset_bps: float
    ladder_slope: float
    ttl_ticks: int
    cancel_age_ticks: int
    soc_target: float
```

### Order program

The action is compiled into an order program:

```python
class OrderProgram:
    mode: StrategyMode
    horizon_ticks: int
    cancel_policy: CancelPolicy
    orders: list[OrderSpec]
    battery_policy: BatteryPolicy
    risk_budget: RiskBudget
    rationale: str | None
```

The program may produce:

```text
OrderIntent[]
CancelIntent[]
ReplaceIntent[]
```

### Initial strategy primitives

The first structured action library may include:

```text
NOOP
HOLD_ENERGY
LIQUIDATE_SURPLUS
COVER_DEFICIT
PASSIVE_MARKET_MAKE
AGGRESSIVE_TAKER
LADDER_SELL
LADDER_BUY
CANCEL_REPRICE
BATTERY_ARBITRAGE
SOC_RECOVERY
SPREAD_CAPTURE
DEFENSIVE_DELOAD
```

This provides much more freedom than Truthful parameter adjustment while remaining trainable, interpretable, and auditable.

### Why not output a raw order book?

Allowing PPO to emit arbitrary bid and ask levels would maximize freedom, but it would also:

- Produce a very high-dimensional action space.
- Reduce sample efficiency.
- Increase invalid actions.
- Make safety enforcement harder.
- Make strategy interpretation and debugging difficult.

Parameterized strategy primitives are the preferred first step.

## 5. Agreed Relationship Between LLM, PPO, and Truthful

The relationship is not a simple pipeline such as:

```text
LLM -> PPO -> Truthful -> order
```

It is a layered collaboration:

```text
                      LLM Strategist
                 slow strategic guidance
                           |
                    StrategyGuidance
                           v
Market/VPP state ---> PPO Executor
                     fast tactical policy
                           |
                     StrategyAction
                           v
                 OrderProgramCompiler
                           |
Truthful Oracle ------> valuation signals
                           |
                           v
                        RiskGate
                           |
                           v
             Order / Cancel / Replace Intents
                           |
                           v
                    Matching Engine
```

### 5.1 LLM: slow strategist, critic, and coach

The LLM operates over a longer time horizon. It should:

- Identify the current market regime.
- Review the outcome of previous strategy windows.
- Recommend or discourage strategy primitives.
- Set a soft risk budget and SOC target.
- Express maker-versus-taker preference.
- Diagnose recurring losses or execution problems.
- Propose new strategy ideas for offline evaluation.

An example output is:

```json
{
  "preferred_modes": [
    "ladder_sell",
    "passive_market_make"
  ],
  "avoid_modes": [
    "aggressive_taker"
  ],
  "risk_budget": 0.4,
  "soc_target": 0.55,
  "execution_style": "Prefer maker orders unless imbalance becomes urgent.",
  "lesson": "The previous window crossed the spread too often."
}
```

The LLM must not directly submit live orders.

Its guidance should normally be represented as:

- Additional PPO observations
- Soft action priors
- Reward-shaping context
- Exploration bias
- Soft primitive preferences

LLM guidance should not become an unconditional hard command, or the design merely replaces the Truthful bottleneck with an LLM bottleneck.

### 5.2 PPO: primary tactical decision-maker

PPO operates on each trading tick and is the main learned execution policy.

It observes:

- Market snapshot and order-book features
- VPP state and DER state
- Energy imbalance and pending energy
- Battery SOC
- Open orders and exposure
- Recent fills and PnL
- Forecast features
- LLM strategic guidance
- Truthful valuation signals

It decides:

- Whether to trade
- Which strategy primitive to use
- Whether to buy, sell, or quote both sides
- How many price levels to use
- Quantity allocation
- Price offsets
- Maker versus taker behavior
- TTL
- Cancellation and repricing behavior
- Battery participation

Example:

```json
{
  "mode": "ladder_sell",
  "aggressiveness": 0.35,
  "qty_fraction": 0.8,
  "ladder_levels": 3,
  "price_slope": 0.04,
  "ttl_ticks": 20
}
```

PPO should have enough freedom to depart materially from Truthful behavior, subject to hard safety constraints.

### 5.3 Truthful: valuation oracle, expert prior, and fallback

Truthful is no longer the normal action generator or policy backbone.

Its new responsibilities are:

1. **Valuation oracle**  
   Estimate fair buy and sell values, marginal generation cost, battery opportunity cost, energy imbalance, and SOC pressure.

2. **Expert prior**  
   Generate demonstrations for behavior cloning or warm-start training.

3. **Risk anchor**  
   Provide economically meaningful reference values for detecting extreme or irrational actions.

4. **Fallback policy**  
   Produce a safe action when PPO is unavailable, invalid, or outside its operating envelope.

Hybrid agents expose this as `fallback_policy`: the default is `hold`, which stands down
when RiskGate vetoes the whole batch; `truthful` is an explicit opt-in for the legacy
Truthful re-quote. Veto-holds are counted by the runner rather than silently traded
through Truthful.

An example signal is:

```python
ValuationSignal(
    fair_buy_price=58.0,
    fair_sell_price=66.0,
    marginal_battery_value=63.0,
    surplus_kwh=1.4,
    deficit_kwh=0.0,
    soc_pressure=-0.2,
)
```

The concise relationship is:

```text
Truthful estimates what the energy is economically worth.
PPO decides how to trade it now.
LLM advises how the agent should behave over the current regime.
RiskGate decides what is actually allowed.
```

### 5.4 RiskGate: final authority

RiskGate is not one of the three intelligence components, but it is essential to their relationship.

It applies to LLM-influenced, PPO-generated, Truthful-fallback, and external-agent actions alike.

It should enforce:

```text
physical energy constraints
SOC minimum and maximum
price bands
quantity and notional limits
inventory and pending-energy exposure
maximum open orders
order-rate limits
cancel-rate limits
VPP ownership
idempotency
tick and deadline validity
late-response rejection
audit logging
```

RiskGate has final veto power. Neither LLM nor PPO may bypass it.

## 6. Time Scales and Authority

| Component | Typical time scale | Primary responsibility | Directly creates final live orders? |
|---|---:|---|---|
| LLM Strategist | Tens or hundreds of ticks | Regime analysis, review, strategic guidance | No |
| PPO Executor | Every tick | Primitive selection and tactical parameters | Indirectly, through the compiler |
| Truthful Oracle | Every tick or when requested | Valuation, expert prior, fallback | Only as fallback |
| OrderProgramCompiler | Every action | Deterministic expansion into intents | Produces candidates |
| RiskGate | Every action | Hard physical, market, and security constraints | Approves, modifies, or rejects |
| Matching Engine | Every accepted intent | Authoritative market execution | Yes, as the sole execution boundary |

The intended authority hierarchy is:

```text
LLM may influence PPO but may not directly trade.
Truthful may inform PPO but may not constrain it to minor residual changes.
PPO may depart from Truthful values but must pass RiskGate.
RiskGate may reject actions from every policy source.
MatchingEngine remains the only authoritative execution mechanism.
```

## 7. PPO Training Direction

The early PPO implementation should be treated as plumbing and a baseline, not as the final policy.

### Recommended progression

#### Stage 1: primitive PPO without LLM

Train:

```text
state
    -> strategy primitive and parameters
    -> order program
    -> market outcome
```

This isolates whether the structured action space is learnable.

#### Stage 2: Truthful imitation warm start

Map Truthful behavior into basic primitives:

```text
surplus sale       -> LIQUIDATE_SURPLUS
deficit purchase   -> COVER_DEFICIT
battery behavior   -> BATTERY_ARBITRAGE
no useful action   -> NOOP
```

Use behavior cloning before PPO fine-tuning to improve sample efficiency and reduce unsafe early exploration.

#### Stage 3: scenario-based training

Progress from a synthetic counterparty to:

```text
one PPO-controlled VPP
    versus
a stable roster of Truthful, ZI, Gas, and other scripted agents
```

Self-play should come later, after scenario-based evaluation is stable.

#### Stage 4: add LLM guidance

Provide the LLM output as structured policy context rather than direct orders.

#### Stage 5: offline strategy evolution

The LLM may propose new primitives or programs, but they must pass:

```text
schema validation
static checks
sandbox simulation
benchmark evaluation
risk tests
approval or promotion criteria
```

Only then may they enter the live primitive library.

### Reward requirements

Reward should not be limited to immediate realized PnL.

It should account for:

```text
realized cashflow
mark-to-market inventory value
unresolved imbalance
liquidity cost
battery degradation
SOC target deviation
invalid actions
excessive order creation
excessive cancellations
risk exposure
```

Conceptually:

```text
reward =
    realized_pnl
  + inventory_value_delta
  - imbalance_penalty
  - liquidity_cost
  - battery_degradation_cost
  - invalid_action_penalty
  - excessive_order_penalty
  - excessive_cancel_penalty
  - soc_target_penalty
```

## 8. Suggested Code Architecture

```text
src/eflux/agents/
  base.py

  truthful.py
  zi.py
  gas.py

  valuation/
    truthful_oracle.py
    schema.py

  strategy/
    schema.py
    primitives.py
    compiler.py

  reflective/
    strategist.py
    prompt.py
    memory.py

  ppo/
    env.py
    sim_env.py
    train.py
    eval.py
    policy.py
    primitive_agent.py

  hybrid/
    agent.py
    risk.py
    diagnostics.py

  remote/
    protocol.py
    registry.py
    agent.py
```

Conceptual orchestration:

```python
class HybridPolicyAgent(BaseAgent):
    strategist: LLMStrategist | None
    executor: PPOPrimitivePolicy
    valuation_oracle: TruthfulValuationOracle
    compiler: OrderProgramCompiler
    risk_gate: RiskGate
    fallback: BaseAgent

    def decide(self, ctx: AgentContext) -> list[OrderIntent]:
        guidance = self.strategist.current_guidance()
        valuation = self.valuation_oracle.estimate(ctx)

        action = self.executor.select_action(
            ctx=ctx,
            guidance=guidance,
            valuation=valuation,
        )

        candidates = self.compiler.compile(
            ctx=ctx,
            action=action,
            valuation=valuation,
        )

        decision = self.risk_gate.validate(ctx, candidates)

        if decision.requires_fallback:
            return self.risk_gate.validate(
                ctx,
                self.fallback.decide(ctx),
            ).accepted

        return decision.accepted
```

## 9. Recommended Implementation Sequence

### Agent intelligence

1. Introduce `StrategyAction`, `StrategyMode`, and `OrderProgram`.
2. Implement a deterministic `OrderProgramCompiler`.
3. Add a small scripted primitive library.
4. Extract or introduce `TruthfulValuationOracle`.
5. Add a reusable RiskGate.
6. Build fixed scenario benchmarks and evaluation metrics.
7. Implement `PPOPrimitiveAgent`.
8. Warm-start PPO from Truthful demonstrations.
9. Upgrade the LLM output to structured `StrategyGuidance`.
10. Assemble `HybridPolicyAgent`.

### External-agent support

1. Define the canonical Agent Protocol.
2. Add state, open-order, batch-submit, and cancellation endpoints.
3. Add idempotency, deadlines, ownership, and audit metadata.
4. Build the Python SDK.
5. Add the MCP adapter.
6. Add asynchronous hosted `RemoteAgent` support only when timeout, caching, fallback, and late-response handling are ready.

## 10. Design Principles to Preserve

1. **Do not place slow LLM calls in the market's critical tick path.**
2. **Do not let LLM output bypass deterministic compilation and risk validation.**
3. **Do not keep Truthful as the hidden policy backbone.**
4. **Do not replace the Truthful bottleneck with hard LLM control.**
5. **Give PPO a meaningful but structured tactical action space.**
6. **Keep the compiler deterministic and independently testable.**
7. **Use one RiskGate for internal, learned, fallback, and external agents.**
8. **Keep the Matching Engine as the only authoritative execution boundary.**
9. **Treat rationale and chain-of-thought-like text as audit/UI metadata, never as execution logic.**
10. **Benchmark each architectural step before increasing autonomy or distribution.**

## 11. Current Working Conclusion

The target architecture is:

```text
LLM = strategic critic, coach, regime selector, and offline strategy researcher
PPO = primary high-frequency tactical execution policy
Truthful = economic valuation oracle, expert prior, risk anchor, and fallback
OrderProgramCompiler = deterministic translation from policy to market actions
RiskGate = final hard-constraint authority
MatchingEngine = sole authoritative execution mechanism
```

The immediate conceptual breakthrough is that EFlux should not merely enlarge the parameter set around Truthful. It should give agents a richer, structured trading language:

```text
strategy primitive
    + execution parameters
    + order/cancel program
    + deterministic risk controls
```

This preserves interpretability and safety while allowing PPO and future policies to learn behavior that is genuinely different from the original Truthful template.
