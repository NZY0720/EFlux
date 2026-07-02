"""EFlux MCP server — exposes the Agent Protocol v1 gateway as MCP tools, so an LLM host
(e.g. Claude Desktop) can read the market and trade through the same SDK/API as any other
external agent (Tier A1). Thin wrappers over ``eflux.sdk.EFluxClient``.

Config via environment:
  EFLUX_MCP_BASE_URL   default http://localhost:8000
  EFLUX_MCP_API_KEY    long-lived API key (preferred), OR
  EFLUX_MCP_EMAIL      dev magic-link login (dev servers only)

Enable + run (requires the `mcp` extra — ``uv sync --extra mcp``):
  EFLUX_MCP_API_KEY=... python -m eflux.mcp.server        # stdio transport
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from eflux.sdk import EFluxClient

mcp = FastMCP("eflux")

_client: EFluxClient | None = None


async def _client_get() -> EFluxClient:
    """Lazily build the authenticated client (API key preferred, dev email as a fallback)."""
    global _client
    if _client is None:
        base = os.environ.get("EFLUX_MCP_BASE_URL", "http://localhost:8000")
        key = os.environ.get("EFLUX_MCP_API_KEY")
        client = EFluxClient(base, token=key)
        if key is None and (email := os.environ.get("EFLUX_MCP_EMAIL")):
            await client.login_dev(email)
        _client = client
    return _client


@mcp.tool()
async def get_market_snapshot(depth: int = 10) -> dict:
    """Current order book (best bid/ask + depth), last price, and supply/demand KPIs."""
    return await (await _client_get()).market_snapshot(depth)


@mcp.tool()
async def get_recent_trades(limit: int = 40) -> list:
    """Recent market-wide trades (the tape) — who traded with whom, and at what price."""
    return await (await _client_get()).recent_trades(limit)


@mcp.tool()
async def list_my_vpps() -> list:
    """The caller's passive, order-driven VPPs."""
    return await (await _client_get()).list_vpps()


@mcp.tool()
async def create_vpp(
    name: str, pv_kw_peak: float = 4.0, battery_kwh: float = 10.0, load_kw_base: float = 1.0
) -> dict:
    """Create a passive VPP to trade with. Returns its id (use it in submit_orders_batch)."""
    return await (await _client_get()).create_vpp(
        name, {"pv_kw_peak": pv_kw_peak, "battery_kwh": battery_kwh, "load_kw_base": load_kw_base}
    )


@mcp.tool()
async def get_open_orders(vpp_id: int) -> list:
    """A VPP's resting orders, so you can reconcile before quoting again."""
    return await (await _client_get()).open_orders(vpp_id)


@mcp.tool()
async def submit_orders_batch(
    orders: list[dict], cancels: list[int] | None = None, idempotency_key: str | None = None
) -> dict:
    """Submit and cancel a batch of orders (Agent Protocol v1). Each order is
    {vpp_id, side: "buy"|"sell", price, qty, client_ref?}. Returns per-item results."""
    return await (await _client_get()).submit_batch(
        orders=orders, cancels=cancels, idempotency_key=idempotency_key
    )


@mcp.tool()
async def cancel_orders(order_ids: list[int]) -> dict:
    """Cancel a list of resting order ids (only your own are touched)."""
    return await (await _client_get()).submit_batch(cancels=order_ids)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
