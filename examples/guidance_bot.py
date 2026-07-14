"""Minimal EFlux bring-your-own-LLM guidance bot (Tier A3).

Your code sets the STRATEGY; the platform keeps doing the EXECUTION. Each cycle this
bot reads the market + its managed agent's performance, decides a StrategyGuidance
(preferred/avoided primitives, risk_budget, soc_target), and PUTs it. The platform's
PPO executor, order compiler, and TradingGatewayV1 turn that steer into actual orders — and
the platform LLM strategist is NOT called while your guidance is active (running your
own model costs zero platform LLM budget). DELETE the guidance to hand control back.

    PYTHONPATH=src python examples/guidance_bot.py --base-url http://localhost:8000 --email me@example.com

The decide() below is a dependency-free heuristic so the example runs anywhere.
Replace it with your own LLM call — see the marked block.

Auth: uses the dev magic-link flow for convenience. In production, mint an API key
(`EFluxClient(base_url, token=API_KEY)`) instead of `login_dev`.
"""

from __future__ import annotations

import argparse
import asyncio

from eflux.sdk import EFluxClient

AGENT_NAME = "my-guided-vpp"
AGENT_PARAMS = {"pv_kw_peak": 4.0, "battery_kwh": 15.0, "battery_kw_max": 5.0, "load_kw_base": 1.0}


def decide(snapshot: dict, performance: dict) -> dict:
    """Turn market + own-agent state into guidance kwargs.

    # ------------------------------------------------------------------
    # plug your own LLM here:
    #   prompt = f"Market: {snapshot}. My agent: {performance}. Reply with JSON guidance."
    #   guidance = json.loads(my_local_model.complete(prompt))
    #   return guidance
    # ------------------------------------------------------------------
    Heuristic stand-in: sell into rich prices, hoard when cheap, cut risk while losing.
    """
    last = snapshot.get("last_price")
    price = float(last) if last else 50.0
    pnl = float(performance.get("pnl") or 0.0)

    if price >= 60.0:  # rich tape: unload stored energy, keep the battery low
        modes = {"preferred_modes": ["liquidate_surplus", "ladder_sell"], "soc_target": 0.25}
        style = f"price {price:.1f} rich — selling down the battery"
    elif price <= 40.0:  # cheap tape: charge up, avoid dumping
        modes = {"preferred_modes": ["battery_arbitrage", "ladder_buy"], "avoid_modes": ["liquidate_surplus"], "soc_target": 0.85}
        style = f"price {price:.1f} cheap — charging"
    else:
        modes = {"preferred_modes": ["passive_market_make"], "soc_target": 0.5}
        style = f"price {price:.1f} mid — passive quoting"

    return {
        "risk_budget": 0.5 if pnl < 0 else 1.0,  # halve size while under water
        "execution_style": style,
        "lesson": "heuristic guidance bot cycle",
        **modes,
    }


async def run(base_url: str, email: str, period: float) -> None:
    async with EFluxClient(base_url) as c:
        await c.login_dev(email)
        managed = {v["name"]: v for v in await c.list_managed_vpps()}
        agent = managed.get(AGENT_NAME) or await c.create_managed_vpp(AGENT_NAME, AGENT_PARAMS)
        managed_id = agent["id"]
        print(f"steering managed agent {managed_id} ({AGENT_NAME}) every {period:.0f}s — ctrl-c to stop")

        while True:
            snap = await c.market_snapshot()
            perf = await c.managed_performance(managed_id)
            guidance = decide(snap, perf)
            res = await c.put_guidance(managed_id, **guidance)
            applied = res["applied"]
            print(
                f"pnl={float(perf['pnl']):+8.2f}  risk={applied['risk_budget']:.2f}  "
                f"soc→{applied['soc_target']:.2f}  prefer={applied['preferred_modes']}  "
                f"({applied['execution_style']})"
            )
            await asyncio.sleep(period)


def main() -> None:
    p = argparse.ArgumentParser(description="EFlux Tier-A3 guidance bot example")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--email", default="guidance-bot@example.com")
    p.add_argument("--period", type=float, default=60.0, help="seconds between guidance updates")
    args = p.parse_args()
    try:
        asyncio.run(run(args.base_url, args.email, args.period))
    except KeyboardInterrupt:
        print("\nstopped (guidance stays active — DELETE /vpps/managed/{id}/guidance to release)")


if __name__ == "__main__":
    main()
