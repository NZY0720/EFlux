# External Participation — Agent Protocol V2

External agents and built-in agents use one execution model. Both emit an
`AgentDecision`; every order, cancel and replace terminates at
`TradingGatewayV2`, uses the same product venue, reservations, USD ledger,
metering and settlement. There is no privileged external or internal route.

## Participation choices

| Mode | You control | Platform controls |
|---|---|---|
| Managed agent | Persona, algorithm and optional LLM guidance | Runtime, PPO/scripted tactics, gateway and settlement |
| External order agent | Read/decide loop and concrete order batches | Authentication, gateway, venue and settlement |
| External guidance | Strategy posture from your own model | Tactical execution, gateway and settlement |

## Physical delivery contract

An order is a commitment for one explicit five-minute delivery product, not an
instantaneous transfer. The request must contain:

- `product_id`: returned by `GET /market/products`;
- `side`: `buy` or `sell`;
- `price`: signed USD/MWh in `[-150, 1000]`;
- `qty_kwh`: terminal energy for that delivery interval;
- `purpose`: the physical resource or obligation behind the order;
- `time_in_force`: `good_til_gate`, `immediate_or_cancel`, or `fill_or_kill`;
- optional `ttl_sec` and `client_ref`.

Purpose has direct physical meaning:

| Purpose | Valid direction | What the gateway reserves/checks |
|---|---|---|
| `balance` | buy or sell | Forecast ambient deficit or renewable surplus |
| `battery` | buy or sell | Gross inverter power, SOC energy and efficiency |
| `dispatchable` | sell | Generator capacity, ramp and delivery energy |
| `flex_load` | buy | Flexible-load headroom and delivery consumption |
| `system_grid` | platform only | External grid liquidity; rejected for user VPPs |

The declaration is necessary because identical market orders can produce
different SOC, power, ramp, metering and settlement consequences. It is not
trusted blindly: inconsistent declarations are rejected before matching.

## Minimal external loop

```python
from eflux.sdk import EFluxClient, Order

async with EFluxClient("http://localhost:8000", token=API_KEY) as client:
    products = await client.products()
    product = next(p for p in products if p["is_open"])
    snapshot = await client.market_snapshot(depth=10)

    result = await client.submit_batch(
        orders=[Order(
            vpp_id=VPP_ID,
            side="buy",
            price=48.0,
            qty=0.2,
            product_id=product["product_id"],
            purpose="balance",
            time_in_force="good_til_gate",
            ttl_sec=120,
            client_ref="balance-001",
        )],
        idempotency_key="decision-001",
    )
```

The canonical HTTP envelope is:

```json
{
  "protocol_version": 2,
  "idempotency_key": "decision-001",
  "deadline": "2026-07-11T05:00:30Z",
  "orders": [{
    "vpp_id": 12,
    "side": "buy",
    "price": 48.0,
    "qty_kwh": 0.2,
    "product_id": "p2p:2026-07-11T05:05:00Z",
    "purpose": "balance",
    "time_in_force": "good_til_gate",
    "ttl_sec": 120,
    "client_ref": "balance-001"
  }],
  "cancels": []
}
```

Batch semantics are per-item and non-atomic. Cancels execute first. A repeated
`idempotency_key` returns the original result. A passed `deadline` rejects the
whole stale request. Ownership is enforced for every VPP and cancellation.

## Gateway validation

For each order the gateway checks, in order:

1. product existence, trading window and gate closure;
2. signed price and positive terminal-kWh bounds;
3. purpose/side compatibility and resource ownership;
4. cumulative cash, energy, power, SOC, ramp and flexible-load reservations;
5. self-trade and realprice grid-counterparty rules;
6. TIF fillability and venue admission.

Accepted fills reserve/update the real-USD ledger. At delivery, meter readings,
contracts and physical resources settle together. Imbalance is charged from the
remaining unserved/spilled energy; replay recomputes the same conservation totals
from the append-only audit stream.

## Reads and reconciliation

| Endpoint | Use |
|---|---|
| `GET /market/products` | Discover product and gate windows |
| `GET /market/snapshot?product_id=...` | Read one product book |
| `GET /market/trades` | Backfill fills |
| `GET /orders/open?vpp_id=...` | Reconcile owned resting orders |
| `POST /orders` | Submit one V2 order |
| `POST /orders/batch` | Retry-safe decision batch |
| `POST /orders/cancel` | Cancel one owned order |
| `WS /ws/market` | Stream order/trade/tick events |

Per-account order submissions share one token bucket: 120-order burst and two
orders per second sustained. Cancels are free. The server returns 429 on
exhaustion; it never relaxes physical checks to satisfy a rate-limited client.

## Guidance-only integration

An owner may send bounded `StrategyGuidance` to a managed agent through
`PUT /vpps/managed/{id}/guidance`. Preferred/avoided modes, SOC target, risk
posture and PPO meta-control are clamped and audited. Guidance never submits raw
orders or bypasses the gateway. Delete the guidance resource to hand strategy
refresh back to the platform LLM.

LLM usage is metered for observability (calls, tokens and estimated cost) but is
not constrained by an application-level cost budget.

## Version policy

V2 is the only supported Agent Protocol in this unreleased project. V1 payloads,
legacy raw-intent routes and old checkpoints are intentionally unsupported.
