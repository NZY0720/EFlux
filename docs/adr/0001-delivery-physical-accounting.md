# ADR 0001 — Delivery products and physical accounting

Status: accepted for Trading Intelligence V2.

## Decision

EFlux trades terminal electrical energy in explicit five-minute delivery
intervals. A product is `[delivery_start, delivery_end)` and its order book
closes no later than `delivery_start`. The default rolling market trades the
next complete interval, never a partially elapsed interval.

Power and energy are different quantities:

```text
terminal_energy_kWh = average_power_kW × duration_hours
```

Orders, fills, positions, and imbalance are terminal kWh measured at the VPP
point of common coupling. Battery SOC is cell kWh. Converting between them must
apply charge/discharge efficiency exactly once.

## Canonical signs

```text
physical net injection =
    renewable generation
  + dispatchable generation
  + battery terminal discharge
  - served load
  - battery terminal charge

contracted net injection = contracted sells - contracted buys

imbalance = physical net injection - contracted net injection
```

Positive imbalance is long/over-delivered. Negative imbalance is
short/under-delivered.

## Battery invariants

- `soc_kwh` always means cell energy and remains within `[0, capacity_kwh]`.
- For terminal charge `E_in`, SOC increases by `E_in × eta_charge`.
- For terminal discharge `E_out`, SOC decreases by `E_out / eta_discharge`.
- `eta_charge × eta_discharge = eta_round_trip`; the default symmetric split is
  `sqrt(eta_round_trip)` per leg.
- Charge/discharge terminal energy in one product is limited by both SOC
  headroom and `max_power_kw × interval_duration_h`.
- Resting orders reserve worst-case cell energy, cell headroom, and interval
  power. Fills convert order reservation into a delivery schedule; cancels and
  expiries release only the unfilled reservation.
- Settlement must never pay a requested quantity when the physical layer
  delivered less. Silent SOC clamping is forbidden in V2 settlement.

## Order backing

The old `dispatched: bool` cannot distinguish physical obligations. V2 orders
carry one explicit purpose:

- `balance`: renewable/load position already present in the base physical net;
- `battery`: scheduled terminal charge/discharge;
- `dispatchable`: fuel-backed scheduled generation;
- `flex_load`: explicitly scheduled flexible demand.

`load_demand_kwh` is uncontrolled/base demand. A filled `flex_load` buy adds
`flexible_load_demand_kwh` at the meter during delivery; it is not counted in
the base load a second time. Resting flexible-load buys reserve interval-level
controllable demand capacity, and only fills become physical consumption.

## Settlement timing

Trading creates contractual positions but does not immediately mutate SOC.
During delivery, the physical engine applies scheduled power and records meter
energy. After the interval ends, settlement compares metered physical injection
with the contractual position, then books imbalance, fuel, degradation,
curtailment, unserved-load, and transaction entries in real USD.

## Consequences

- The existing immediate `_settle_cash_and_energy` path will be replaced.
- PPO training must use the same product, reservation, physical, and settlement
  pipeline as live execution.
- Existing checkpoints are invalid because their action cadence, reward scale,
  and cash units describe a different environment.
- Negative prices are valid prices; positivity checks apply to quantity, not to
  price.
