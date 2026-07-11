# EFlux V2 Architecture and Design

This document describes the current unreleased architecture. V1 paths and
checkpoint compatibility are intentionally out of scope.

## System shape

```text
physical state + forecasts + product books + open commitments
                          |
                    AgentContext
                          |
       TruthfulValuationOracle (economic/physical signal)
                          |
     scripted / PPO V4 / LLM-guided strategy policy
                          |
                    StrategyAction
                          |
                OrderProgramCompiler
                          |
                    AgentDecision
            (orders / cancels / replaces)
                          |
                 TradingGatewayV2
       validation + reservations + USD ledger
                          |
             ProductMatchingEngine (CDA)
                          |
        contracts -> dispatch -> meter -> settle
                          |
              append-only audit + replay
```

The gateway is the final execution authority. Internal agents, fallbacks,
external HTTP/SDK/MCP clients and the system-grid participant do not have
separate matching or settlement paths.

## Time and products

- Physical simulation cadence: one second.
- Built-in tactical decision cadence: 30 seconds.
- Product duration: five minutes.
- Six consecutive products are visible by default.
- Each product has open, gate-closure, delivery-start and delivery-end times.
- Orders use terminal kWh for the named product; physical devices use kW and
  internal battery-cell kWh.

The conversion boundary is explicit: average terminal power multiplied by
interval hours equals terminal energy. Battery charge/discharge applies
square-root round-trip efficiency on the correct side of that boundary. Gross
charge plus discharge power may not exceed the inverter limit.

## Agent intelligence

`AgentContext` is a point-in-time snapshot containing DER state, SOC, cash/PnL,
forecasts, product intervals, market depth, open orders, contracted energy,
projected net energy and rejection history.

`TruthfulValuationOracle` derives marginal buy/sell and battery values plus
physical surplus/deficit. It is a signal provider, not the final policy.

Policies choose a bounded, auditable `StrategyAction`. The current tactical
library includes imbalance cover/liquidation, battery arbitrage, grid
charge/discharge and wait behavior. The compiler deterministically lowers that
action into `OrderRequest`, `CancelRequest` and `ReplaceRequest` entries within
one `AgentDecision`.

PPO V4 observes 33 channels, including robust signed-price features, forecasts,
gate timing, horizons, cash, open commitments, dispatchable state and residual
contract exposure. Separate action profiles/checkpoints are trained for P2P and
realprice modes. Behavior cloning mixes the primitive environment with live
multi-agent topology demonstrations; official evaluation freezes the policy and
uses isolated paired worlds over multiple seeds.

The LLM is a slow strategist. It emits clamped guidance and PPO meta-control;
it never emits raw executable orders. Its feedback window includes realized and
mark-to-market PnL changes, trades/cash, rejection deltas, imbalance, residual
contracts, SOC and open orders. Calls, tokens and estimated cost are metered for
observability, with no application-level cost budget or call blocker.

## Trading mechanism

P2P uses one continuous double-auction book per delivery product:

- price-time priority;
- fills at the resting order price;
- signed prices in `[-150, 1000] USD/MWh`;
- no self-trade;
- good-til-gate, IOC and FOK;
- explicit TTL capped by gate closure;
- seeded fair arrival order for simultaneous built-in decisions.

Realprice is a price-taking grid venue. The system-grid participant publishes
the executable import/export liquidity; peer-to-peer matching is disabled for a
realprice product. This keeps grid cash and energy visible in the same ledger and
audit stream without inventing a fake battery or ranking participant.

## Physical purpose and reservations

Every order declares one purpose:

- `balance`: ambient load deficit or renewable surplus;
- `battery`: charge or discharge;
- `dispatchable`: generator sale;
- `flex_load`: controllable consumption purchase;
- `system_grid`: reserved platform grid liquidity.

Purpose is required because the same market side/quantity can imply different
SOC, efficiency, ramp, metering and settlement effects. The gateway verifies the
declaration against the participant portfolio and reserves cumulative resources
across resting orders and fills. It checks cash, balance energy, battery terminal
energy and gross inverter power, dispatchable capacity/ramp and flexible-load
headroom. Cancels, expiry, fills and settlement release or roll reservations
exactly once.

## Delivery and settlement

After gate closure, contracts are immutable. During delivery the simulator:

1. applies ambient PV/wind/load;
2. dispatches contracted battery, dispatchable and flexible-load schedules;
3. respects one-second power/ramp/SOC constraints;
4. integrates terminal import/export energy;
5. settles contract cash in real USD;
6. charges remaining unserved load/spilled generation at imbalance prices;
7. records ending SOC, meter and reservation state.

The core conservation identity for every participant/product is:

```text
metered terminal supply + contracted net import + unserved load
= metered terminal demand + contracted net export + spilled generation
```

Cash is double-entry across peers/system grid plus explicitly named fees,
degradation and imbalance accounts. No hidden normalization units are used in
the public ledger.

## Audit and deterministic replay

Orders, cancellations, fills, reservations, physical dispatch, meters and
settlement are appended to the audit database with simulation and wall times.
Replay reconstructs positions and conservation totals without executing agent
code. Given the same scenario, seed and inputs, scheduling and replay results are
deterministic.

## External boundary

Agent Protocol V2 is the only supported external contract. Each order includes
`product_id`, `qty_kwh`, physical `purpose`, TIF and optional TTL/client reference.
Batch requests add idempotency, deadline and cancels-first semantics. See
[AGENT_SPEC.md](AGENT_SPEC.md) and
[EXTERNAL_PARTICIPATION.md](EXTERNAL_PARTICIPATION.md).

## Acceptance gates

A release candidate must pass:

- unit and integration suites plus static lint/type checks;
- per-product energy, battery and USD conservation;
- deterministic audit replay across multiple seeds;
- negative-price, gate, TIF, reservation and self-trade tests;
- frozen paired-world PPO/LLM evaluation with reported deltas, not only accuracy;
- load/stress runs covering many agents/products/orders;
- decision-loop and matching latency thresholds;
- frontend production build and generated OpenAPI/schema parity.
