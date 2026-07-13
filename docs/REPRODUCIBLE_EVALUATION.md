# Reproducible evaluation and evidence

EFlux has one evidence model across Prove-out, managed competition evaluation and
backtests. A manifest records engine version, commit and executable source-tree hash, dirty-source state, protocol and
rules versions, scenario/config hashes, seed labels, model hashes and input-data hashes.
The evidence id excludes only the creation timestamp.

## Prove-out

The managed historical battery strategy submits five-minute product orders to
`TradingGatewayV2`. The gateway performs credit and physical reservation checks; fills,
delivery, imbalance settlement and degradation use the normal V2 path. Cached hourly CAISO
LMPs are currently repeated over the twelve five-minute products in each source hour and
are labeled that way in both report and manifest.

Owners can download `GET /prove-out/runs/{id}/evidence`. The JSON contains the manifest,
complete audit envelope, reconstructed-state hash, order attribution and candidate ledger.

## Competition

An open competition uses hidden seeds for provisional evaluation. A participant explicitly
selects one scored submission with `POST /submissions/{id}/select-final`. An administrator
closes the round with `POST /competitions/{slug}/close`; this freezes selections and queues
holdout runs from immutable submission/rules snapshots. A closed leaderboard reads only
holdout results. Hidden and holdout seed values are derived with the independent
`EFLUX_EVALUATION_SEED_KEY` and are never stored in an API response.

Evaluation evidence is embargoed while the round is open. After close, an owner can download
`GET /evaluation-runs/{id}/evidence`.

## Scenario and comparison commands

```bash
uv run eflux scenario validate scenarios/p2p.yaml
uv run eflux scenario inspect scenarios/p2p.yaml
uv run eflux scenario hash scenarios/p2p.yaml
uv run eflux scenario normalize legacy.yaml --output scenario-v1.yaml
uv run eflux compare artifacts/backtests/LEFT artifacts/backtests/RIGHT
```

Comparison reports are descriptive right-minus-left deltas. One run per side is not enough
to estimate a confidence interval, so the report returns no interval and makes no causal
claim. Paired replicate inference can be added when runs record a shared replicate design.
