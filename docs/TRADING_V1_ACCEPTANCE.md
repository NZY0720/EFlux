# Trading Intelligence V1 acceptance

## Outcome

V1 is the only supported EFlux application, Agent Protocol, gateway, forecast
state and PPO checkpoint contract. The strongest validated candidate weights
were promoted into this contract; earlier numbered artifacts and compatibility
paths were removed.

## Canonical artifacts

The repository carries exactly two production warm starts plus their manifest:

- `checkpoints/bc_primitive_p2p_v1.pt`
- `checkpoints/bc_primitive_realprice_grid_v1.pt`
- `checkpoints/manifest.v1.json`

Both checkpoints use the `bc_primitive_v1` envelope, encoding version 1 and the
33-channel observation version 1. Bare state dictionaries and other metadata
versions fail closed.

The manifest pins the training recipe, serving contract, file sizes and SHA-256
digests. The P2P artifact digest is
`6ca32c12976487d4e2809b4b73dba26bf392d169d7111c69336265458035ea8f`; the
real-price-grid artifact digest is
`d085ddf271208260b1b5bb22af0622aa09b23e4317f343afc015266ed3d0bc84`.

## Promotion evidence

The promoted weights retain the same 33-input network architecture as the
immediately preceding candidate but contain different trained parameters. A
deterministic pure-battery curriculum reproduces each promoted state dict to
floating-point tolerance. On the isolated P2P battery probe, the selected
candidate emits non-zero battery orders on 206 of 288 decisions, compared with
9 of 288 for the superseded candidate.

This evidence establishes lineage and competence, not economic uplift. Official
uplift claims still require frozen, isolated paired-world evaluation across
multiple hidden seeds.

## Contract reset

- Package version: `1.0.0`.
- Agent Protocol and trading gateway: V1.
- PPO action encoding and observation schema: V1.
- Forecast persisted-state model: `online-rls-v1`.
- Competition rules: `rules-v1`.
- Ecosystem recipe and environment schema versions: `1`.

No field is silently translated from a pre-reset application version. External
vendor model names, dependency versions, third-party API paths, data-provider
versions and database migration revision IDs keep their native identifiers.

## Required verification

The release gate runs backend lint and tests, database migration rehearsal,
generated-contract parity, checkpoint-manifest validation and a production
frontend build. Conservation, replay, signed-price, reservation, settlement and
paired-evaluation tests remain part of the backend suite.
