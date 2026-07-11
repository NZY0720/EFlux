# Trading Intelligence & Market Mechanism V2

This document is the implementation index for the unreleased V2 redesign. No
V1 compatibility is required. Old checkpoints and generated results were
deleted when the branch was created.

The current verification record is
[TRADING_V2_ACCEPTANCE.md](TRADING_V2_ACCEPTANCE.md).

## Accepted defaults

- 1-second physical simulation ticks.
- 30-second built-in tactical decisions.
- Five-minute delivery products, traded before delivery starts.
- P2P continuous double auction with price-time priority and resting-order price.
- Signed prices with an initial `[-150, 1000] $/MWh` band.
- Real USD in the economic ledger.
- Frozen policies in official evaluation; online learning is sandbox-only.
- LLM guidance controls bounded strategy posture, not the objective function.

## Critical path

1. Delivery/physical invariants and executable product model.
2. Real-USD economic ledger and terminal inventory valuation.
3. Resource reservations and physically correct battery/gas delivery.
4. Gate closure, interval settlement, imbalance, and negative prices.
5. Unified AgentDecision with order/cancel/replace/TIF/horizon semantics.
6. Snapshot-consistent decisions and seeded arrival-order fairness.
7. One runtime path for internal and external participants.
8. PPO observation/reward parity, retraining, and hidden-seed evaluation.
9. LLM window feedback and isolated paired-world uplift measurement.
10. Market-quality stress tests, leaderboard, API, SDK, MCP, and UI completion.

Every phase must add tests that prove its invariants. Passing legacy behavior
tests is not sufficient when the legacy behavior conflicts with V2 economics.
