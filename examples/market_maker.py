"""Minimal EFlux market-making agent using the Python SDK (Tier A1).

Each cycle it reads the market, quotes a two-sided spread around the mid, cancels its stale
quotes, and submits the new quotes as one Agent Protocol V1 batch (cancels-first). Educational,
not tuned — a starting point for your own read -> decide -> submit_batch loop.

    PYTHONPATH=src python examples/market_maker.py --base-url http://localhost:8000 --email me@example.com

Auth: uses the dev magic-link flow for convenience. In production, mint an API key
(`EFluxClient(base_url, token=API_KEY)`) instead of `login_dev`.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from eflux.sdk import EFluxClient, Order


def _mid(best_bid, best_ask, last) -> float | None:
    if best_bid and best_ask:
        return (float(best_bid) + float(best_ask)) / 2
    for x in (last, best_bid, best_ask):
        if x:
            return float(x)
    return None


async def run(base_url: str, email: str, spread: float, size: float, sleep: float) -> None:
    async with EFluxClient(base_url) as c:
        await c.login_dev(email)
        vpp = await c.create_vpp(
            f"mm-{uuid.uuid4().hex[:6]}", {"pv_kw_peak": 4.0, "battery_kwh": 10.0}
        )
        vpp_id = vpp["id"]
        print(f"market-making as vpp {vpp_id} (spread={spread}, size={size})")

        while True:
            product = next(p for p in await c.products() if p["is_open"])
            product_id = product["product_id"]
            snap = await c.market_snapshot()
            mid = _mid(snap.get("best_bid"), snap.get("best_ask"), snap.get("last_price"))
            if mid is None:
                print("no price yet, waiting…")
                await asyncio.sleep(sleep)
                continue

            bid = round(mid * (1 - spread), 2)
            ask = round(mid * (1 + spread), 2)
            stale = [o["order_id"] for o in await c.open_orders(vpp_id)]

            res = await c.submit_batch(
                orders=[
                    Order(vpp_id, "buy", bid, size, product_id, "battery", client_ref="bid"),
                    Order(vpp_id, "sell", ask, size, product_id, "battery", client_ref="ask"),
                ],
                cancels=stale,  # replace last cycle's quotes
                idempotency_key=uuid.uuid4().hex,
            )
            fills = sum(len(r.get("trades") or []) for r in res["results"])
            rejected = [r for r in res["results"] if r["status"] == "rejected"]
            print(
                f"mid={mid:7.2f}  quote {bid}/{ask}  replaced={len(stale)}  "
                f"fills={fills}  rejected={len(rejected)}  tokens={res['rate_limit_remaining']}"
            )
            await asyncio.sleep(sleep)


def main() -> None:
    p = argparse.ArgumentParser(description="EFlux market-making example agent")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--email", default="agent@example.com")
    p.add_argument("--spread", type=float, default=0.02, help="half-spread as a fraction of mid")
    p.add_argument("--size", type=float, default=0.1, help="quote size in kWh")
    p.add_argument("--sleep", type=float, default=3.0, help="seconds between cycles")
    args = p.parse_args()
    try:
        asyncio.run(run(args.base_url, args.email, args.spread, args.size, args.sleep))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
