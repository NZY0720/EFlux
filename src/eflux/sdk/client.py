"""EFlux Python SDK — a thin async client for external (Tier A1) agents.

Wraps auth, public market-data reads, VPP management, and the Agent Protocol v2 order
endpoints so an agent author can write a `read → decide → submit_batch` loop without
hand-rolling HTTP. See docs/AGENT_SPEC.md §5.

    async with EFluxClient("http://localhost:8000") as c:
        await c.login_dev("me@example.com")          # or EFluxClient(token=API_KEY)
        vpp = await c.create_vpp("my-bot", {"pv_kw_peak": 4.0, "battery_kwh": 10.0})
        product = (await c.products())[0]
        await c.submit_batch([
            Order(vpp["id"], "sell", 55.0, 1.0, product["product_id"], "balance")
        ])
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


class EFluxError(RuntimeError):
    """A non-2xx response from the EFlux API, with the server's detail message extracted."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.message = message


@dataclass
class Order:
    """One order in a batch. price/qty accept float, str, or Decimal."""

    vpp_id: int
    side: str  # "buy" | "sell"
    price: float | str | Decimal
    qty: float | str | Decimal
    product_id: str
    purpose: str
    time_in_force: str = "good_til_gate"
    ttl_sec: float | None = None
    client_ref: str | None = None


def _order_json(o: Order | dict) -> dict[str, Any]:
    if isinstance(o, Order):
        vpp_id, side, price, qty = o.vpp_id, o.side, o.price, o.qty
        product_id, purpose = o.product_id, o.purpose
        time_in_force, ttl_sec, ref = o.time_in_force, o.ttl_sec, o.client_ref
    else:
        vpp_id, side, price = o["vpp_id"], o["side"], o["price"]
        qty = o["qty_kwh"] if "qty_kwh" in o else o["qty"]
        product_id, purpose = o["product_id"], o["purpose"]
        time_in_force = o.get("time_in_force", "good_til_gate")
        ttl_sec = o.get("ttl_sec")
        ref = o.get("client_ref")
    body: dict[str, Any] = {
        "vpp_id": vpp_id,
        "side": side,
        "price": str(price),
        "qty_kwh": str(qty),
        "product_id": product_id,
        "purpose": purpose,
        "time_in_force": time_in_force,
    }
    if ttl_sec is not None:
        body["ttl_sec"] = ttl_sec
    if ref is not None:
        body["client_ref"] = ref
    return body


def _detail(resp: httpx.Response) -> str:
    """Extract FastAPI's error detail (string or validation list) into a readable message."""
    try:
        detail = resp.json().get("detail")
    except Exception:
        return resp.text[:200]
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
            msg = item.get("msg", "")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "; ".join(p for p in parts if p) or resp.text[:200]
    return str(detail) if detail else resp.text[:200]


class EFluxClient:
    """Async EFlux API client. Pass ``token`` (an API key or session token) directly, or call
    ``login_dev`` against a dev server. Provide ``http`` to reuse an existing httpx client
    (e.g. an in-process ASGI transport in tests)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        token: str | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._token = token
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def __aenter__(self) -> EFluxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @property
    def token(self) -> str | None:
        return self._token

    def set_token(self, token: str | None) -> None:
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> Any:
        resp = await self._http.request(
            method, path, json=json, params=params, headers=self._headers()
        )
        if resp.status_code >= 400:
            raise EFluxError(resp.status_code, _detail(resp))
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # --- auth ---

    async def login_dev(self, email: str) -> str:
        """Dev-only magic-link flow (the dev server echoes the token). Stores + returns a
        session token. Production integrators should mint an API key and pass it as ``token``."""
        body = await self._request("POST", "/auth/magic-link", json={"email": email})
        token = body.get("dev_token")
        if not token:
            raise EFluxError(400, "no dev_token in response (server not in dev mode?)")
        session = await self._request("POST", "/auth/consume", json={"token": token})
        self._token = session["session_token"]
        return self._token

    async def mint_api_key(self, name: str) -> str:
        """Mint a long-lived API key (requires an existing session/token)."""
        body = await self._request("POST", "/auth/api-keys", json={"name": name})
        return body["key"]

    # --- VPPs ---

    async def create_vpp(self, name: str, params: dict) -> dict:
        return await self._request("POST", "/vpps", json={"name": name, "params": params})

    async def list_vpps(self) -> list[dict]:
        return await self._request("GET", "/vpps")

    # --- Managed agents (Tier 0) + external guidance (Tier A3) ---

    async def create_managed_vpp(
        self,
        name: str,
        params: dict,
        *,
        persona: str | None = None,
        agent_params: dict | None = None,
        seed: int | None = None,
        model: str | None = None,
    ) -> dict:
        """Provision a platform-hosted managed agent (Tier 0) the simulator drives."""
        return await self._request(
            "POST",
            "/vpps/managed",
            json={
                "name": name,
                "params": params,
                "persona": persona,
                "agent_params": dict(agent_params or {}),
                "seed": seed,
                "model": model,
            },
        )

    async def list_managed_vpps(self) -> list[dict]:
        return await self._request("GET", "/vpps/managed")

    async def managed_performance(self, managed_id: int) -> dict:
        """PnL, SOC, recent trades, and the guidance/reflection timeline."""
        return await self._request("GET", f"/vpps/managed/{managed_id}/performance")

    async def put_guidance(
        self,
        managed_id: int,
        *,
        preferred_modes: list[str] | tuple[str, ...] = (),
        avoid_modes: list[str] | tuple[str, ...] = (),
        risk_budget: float = 1.0,
        soc_target: float | None = None,
        execution_style: str = "",
        lesson: str = "",
        meta_control: dict | None = None,
    ) -> dict:
        """Steer your managed agent with your own model (Tier A3). The platform LLM
        stops being consulted while your guidance is active; values are clamped
        server-side and the response echoes what was applied. ~2/min rate limit."""
        payload = {
            "preferred_modes": list(preferred_modes),
            "avoid_modes": list(avoid_modes),
            "risk_budget": risk_budget,
            "execution_style": execution_style,
            "lesson": lesson,
            "meta_control": meta_control,
        }
        if soc_target is not None:
            payload["soc_target"] = soc_target
        return await self._request(
            "PUT",
            f"/vpps/managed/{managed_id}/guidance",
            json=payload,
        )

    async def release_guidance(self, managed_id: int) -> None:
        """Hand steering back to the platform LLM strategist."""
        await self._request("DELETE", f"/vpps/managed/{managed_id}/guidance")

    # --- market data (public) ---

    async def market_snapshot(self, depth: int = 10) -> dict:
        return await self._request("GET", "/market/snapshot", params={"depth": depth})

    async def products(self) -> list[dict]:
        return await self._request("GET", "/market/products")

    async def recent_trades(self, limit: int = 200) -> list[dict]:
        return await self._request("GET", "/market/trades", params={"limit": limit})

    async def recent_ticks(self, limit: int = 100_000) -> list[dict]:
        """Current-session price ticks, oldest first, for refresh recovery."""
        return await self._request("GET", "/market/ticks", params={"limit": limit})

    async def participants(self) -> list[dict]:
        return await self._request("GET", "/market/participants")

    async def supply_curve(self) -> dict:
        return await self._request("GET", "/market/supply_curve")

    # --- orders (Agent Protocol v2) ---

    async def open_orders(self, vpp_id: int) -> list[dict]:
        """This VPP's resting orders — reconcile without scraping the whole market."""
        return await self._request("GET", "/orders/open", params={"vpp_id": vpp_id})

    async def submit_order(
        self,
        vpp_id: int,
        side: str,
        price: float | str | Decimal,
        qty: float | str | Decimal,
        *,
        product_id: str,
        purpose: str,
        time_in_force: str = "good_til_gate",
        ttl_sec: float | None = None,
    ) -> dict:
        order = Order(
            vpp_id,
            side,
            price,
            qty,
            product_id,
            purpose,
            time_in_force,
            ttl_sec,
        )
        return await self._request("POST", "/orders", json=_order_json(order))

    async def cancel(self, order_id: int) -> None:
        await self._request("POST", "/orders/cancel", json={"order_id": order_id})

    async def submit_batch(
        self,
        orders: list[Order | dict] | None = None,
        cancels: list[int] | None = None,
        *,
        idempotency_key: str | None = None,
        deadline: Any = None,
    ) -> dict:
        """Agent Protocol v2 batch: submit orders + cancels in one call. Returns the
        per-item result envelope (see docs/AGENT_SPEC.md §5)."""
        payload: dict[str, Any] = {
            "protocol_version": 2,
            "orders": [_order_json(o) for o in (orders or [])],
            "cancels": list(cancels or []),
        }
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        if deadline is not None:
            payload["deadline"] = (
                deadline.isoformat() if hasattr(deadline, "isoformat") else deadline
            )
        return await self._request("POST", "/orders/batch", json=payload)
