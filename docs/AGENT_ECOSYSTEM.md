# Agent Release ecosystem

EFlux publishes Agent versions as immutable Releases. Publishing binds the recipe, state, compatibility metadata, and runtime definition to a content SHA-256. Evaluations then bind their evidence to that exact Release hash.

The platform reports evidence rather than a universal winner. Results retain the asset profile, protocol, seed set, cost assumptions, provenance, per-seed outcomes, and uncertainty statistics. Users decide which trade-offs fit their own deployment; EFlux does not collapse the evidence into a mandatory composite score.

## Runnable example

From the repository root:

```bash
PYTHONPATH=src .venv/bin/python examples/agent_ecosystem_demo.py
```

The example is isolated from `eflux_dev.db`. It creates a temporary SQLite database, calls the real ecosystem service to create and publish a scripted realprice Release, queues a deterministic evaluation, and runs the real worker once. The printed JSON contains:

- the immutable Release content hash;
- the platform-assigned evidence provenance;
- the evidence hash and full evaluation context;
- separate economic, risk, cost, and data-quality metrics.

The short protocol uses one seed and 12 intervals so the example completes quickly. It demonstrates plumbing, immutability, provenance, and evidence binding—not statistical sufficiency. Production comparisons should use the standard multi-seed protocol and inspect distributions or confidence intervals.

The same flow is available through the REST API:

```text
POST /agent-releases
POST /agent-releases/{id}/publish
POST /agent-releases/{id}/evaluations
GET  /agent-releases/{id}/evaluations
```

The API request queues platform work; `./tasks.sh ecosystem-worker` processes that queue.
For local development, `./tasks.sh dev-stack` starts the API together with the evaluation
and ecosystem workers. `./tasks.sh run` starts only the API.

## Evidence modes

- Deterministic replay uses the immutable Release hash and fixed seeds. An LLM Release must provide per-seed archived transcripts; every prompt and response hash is checked before reuse.
- Fresh-LLM historical replay loads a platform historical price window, calls the currently configured model under strict failure handling, and archives prompts, responses, model, date, latency, tokens, and cost. It is explicitly not labeled deterministic.
- P2P formal evaluation runs isolated treatment/control worlds across public Population Packs plus worker-only hidden rosters. PnL uplift, tail outcomes, imbalance, rejections, volume, spread, depth, price volatility, and a limit-price surplus proxy remain separate evidence dimensions.
- Forward shadow and verified live evidence are accepted only from runtime snapshots bound to the exact Release ID and content hash.

No mode emits a mandatory composite Agent score. Approximate confidence intervals and complete per-seed evidence are retained so a user can apply their own priorities.

## Dataset and derived-Agent flow

Platform trajectory export starts from a completed persisted market session:

```text
POST /market-sessions/{session_id}/behavior-datasets
POST /behavior-datasets/{dataset_id}/publish
POST /behavior-datasets/{dataset_id}/train
GET  /training-runs/{run_id}
```

Publishing scans the gzip JSONL artifact itself. It verifies observation, action, gateway execution, no-op/rejection/unfilled fields, delivery outcome, redaction, row count, containment, and SHA-256 instead of trusting manifest claims. BC produces a draft PPO-compatible warm-start Release. PPO fine-tuning requires an owner-controlled warm start, runs in the closed-loop sandbox, and must pass a hidden non-learning risk episode before producing another draft Release.

Self-reported imports cannot assign themselves trusted provenance. For a broker or external platform integration, configure `EFLUX_EXTERNAL_ATTESTATION_KEYS` as a JSON map from provider ID to shared signing secret. The owner first requests `GET /behavior-datasets/{id}/attestation-payload`, the provider signs the returned canonical UTF-8 payload with HMAC-SHA256, and the owner submits the signature to `POST /behavior-datasets/{id}/attest`. The payload binds provider, issue time, dataset/owner IDs, name/version, market/schema, and artifact hash. Only a valid configured signature changes provenance to `externally_attested`; the artifact then becomes non-replaceable.

## Safe deployment

Publishing validates the complete recipe, runtime identity, compatibility contract, LLM placeholders, and PPO checkpoint. Deployments start in shadow, paper, or live mode. Live creation and `POST /agent-deployments/{id}/promote-live` require an explicit risk acknowledgement plus completed platform evidence. Promotion changes only execution mode; positions, cash, learning state, LLM memory, logs, Release ID, and Release hash remain on the same independent instance.
