# Trading Intelligence V2 Acceptance — 2026-07-11

## Outcome

The V2 implementation passes its functional, conservation, replay, API,
frontend, stress and performance gates. PPO V4 warm starts are close clones of
the battery-aware demonstrator across ten isolated seeds; they do not claim
economic uplift over the policy they were trained to imitate.

## Build and contract checks

- Ruff: all `src`, `tests` and `examples` checks passed.
- Unit suite: 462 passed.
- Integration suite: 83 passed in the full run; the one stale Redis TickEvent
  fixture was migrated to V2 delivery fields and passed on immediate rerun
  (84/84 integration cases verified).
- Frontend: `tsc -b && vite build` passed.
- `docs/openapi.json`, `docs/agent_spec.schema.json` and
  `frontend/src/api/schema.gen.ts` were regenerated from the current code.
- Focused delivery/gateway/conservation/replay suite: 42 passed.

The integration suite migration removed the last live Protocol V1 payloads.
External test orders now specify `qty_kwh`, `product_id`, physical `purpose`
and V2 protocol version. PPO V1 action/observation tests and loaders were also
removed; only the current V2 action encoding is accepted.

## PPO V4 training

Both checkpoints were regenerated from the fixed 2026-06-10 through 2026-07-10
CAISO/Open-Meteo window using seed `20260711`:

```text
40 primitive episodes
10 live-topology episodes × 288 decisions
500 class-balanced behavior-cloning epochs
price normalization reference = 19.97 USD/MWh
observation = V4 (33 channels)
```

Artifacts:

- `checkpoints/bc_primitive_p2p_v4.pt`
- `checkpoints/bc_primitive_realprice_grid_v4.pt`

Training-set results:

| Checkpoint | Samples | Mode accuracy | Trade accuracy | Primitive reward | Random reward |
|---|---:|---:|---:|---:|---:|
| P2P V4 | 4,800 | 0.916 | 0.924 | -0.16 | -0.17 |
| realprice V4 | 4,800 | 0.857 | 0.857 | -0.17 | -0.17 |

The decoder snaps only a narrow neighborhood around exact neutral execution
defaults. This prevents tiny neural-fit residuals in fair-price multiplier,
offset or full physical quantity from moving an otherwise correct order across
the spread. Values outside the deadband remain continuous for online PPO.

## Ten-seed isolated paired evaluation

Treatment and control run in separate worlds with identical seed, endowment,
counterparties, product clock and price scale. Each pair covers 288 five-minute
decisions; control is the battery-aware demonstrator.

| Market | Mean MTM delta | Median MTM delta | Mean imbalance delta | Rejection delta |
|---|---:|---:|---:|---:|
| P2P | -0.0011705 USD/day | -0.0012785 USD/day | -0.20057 kWh/day | 0 |
| realprice | -0.0016685 USD/day | -0.0017255 USD/day | -0.09287 kWh/day | 0 |

All individual deltas are slightly negative, so raw win rate is zero. That is
reported rather than hidden: this checkpoint is a compressed warm start of the
expert, not an independently optimized policy. The parity acceptance tolerance
is `|mean MTM delta| <= 0.005 USD/day`, mean additional imbalance
`<= 0.25 kWh/day`, and no additional gateway rejections; both profiles pass.
Economic uplift must be evaluated after online learning, against an isolated
frozen control, and may not be inferred from cloning accuracy.

## Conservation and replay

The focused 42-case gate covers:

- terminal kWh ↔ average kW conversion and five-minute product boundaries;
- battery charge/discharge efficiency, gross inverter power and SOC;
- dispatchable/flexible/balance reservations and settlement release;
- signed prices, price-time priority, TIF, gate closure and no self-trade;
- real-USD double entry and imbalance settlement;
- append-only sequence, cash and physical delivery replay corruption checks;
- seeded simultaneous-decision fairness and determinism.

All passed. Multi-seed paired worlds also completed with zero added gateway
rejections and deterministic treatment/control isolation.

## Stress and performance

Measured locally on this development machine, without network I/O:

| Test | Result |
|---|---:|
| Product venue, 20,000 alternating orders over six products | 98,525 orders/s |
| Submit latency p50 / p95 / p99 | 0.0099 / 0.0107 / 0.0139 ms |
| Multi-agent simulator, 1,000 one-second intervals | 16.99 s total |
| Simulator throughput | 58.9 intervals/s (16.99 ms/interval) |

This is comfortably faster than the one-second physical cadence. These figures
are regression baselines for this machine, not cross-hardware service SLOs.

## LLM cost handling

There is no application-level LLM cost budget, reserve, skip or budget-exceeded
exception. `LLMUsageMeter` retains call, prompt-token, completion-token and
estimated-cost telemetry only. LLM guidance remains bounded by strategy/gateway
safety, concurrency and cadence controls rather than cost blocking.
