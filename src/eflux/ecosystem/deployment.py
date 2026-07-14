"""Reusable deployment compatibility and risk policy."""

from __future__ import annotations

import re
from typing import Any

from eflux.db.models import AgentRelease

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InvalidDeployment(ValueError):
    """The requested deployment does not satisfy its release contract."""


class UnsafeLiveDeployment(RuntimeError):
    """The process gateway is looser than the release's declared limits."""


def assert_deployment_compatibility(
    release: AgentRelease,
    *,
    profile: dict[str, Any],
    profile_id: str,
    params: dict[str, Any],
    available_credit_usd: float,
    decision_interval_seconds: float,
    product_granularity_seconds: float,
) -> None:
    compatibility = dict(release.compatibility or {})
    declared_market = compatibility.get("market")
    if declared_market is not None and declared_market != release.market:
        raise InvalidDeployment("release compatibility market does not match the Release")

    exact_profile = compatibility.get("profile_id")
    if isinstance(exact_profile, str) and profile_id != exact_profile:
        raise InvalidDeployment(f"release requires asset profile {exact_profile!r}")
    profile_ids = compatibility.get("profile_ids")
    if isinstance(profile_ids, list) and profile_ids and profile_id not in profile_ids:
        raise InvalidDeployment(f"asset profile {profile_id!r} is not supported by this release")
    vpp_types = compatibility.get("vpp_types")
    asset_type = str(profile.get("spec", {}).get("asset_type") or "")
    if isinstance(vpp_types, list) and vpp_types and asset_type not in vpp_types:
        raise InvalidDeployment(f"asset type {asset_type!r} is not supported by this release")

    for field in (
        "battery_kwh",
        "battery_kw_max",
        "pv_kw_peak",
        "load_kw_base",
        "wind_kw_rated",
        "gas_kw_max",
    ):
        bounds = compatibility.get(f"{field}_range")
        if isinstance(bounds, list) and len(bounds) == 2:
            value = float(params.get(field, 0.0))
            if not float(bounds[0]) <= value <= float(bounds[1]):
                raise InvalidDeployment(f"{field}={value} is outside release compatibility")

    minimum_cash = float(compatibility.get("minimum_cash_usd", 0.0))
    starting_cash = float(params.get("starting_cash_usd", 0.0))
    if starting_cash < minimum_cash:
        raise InvalidDeployment(
            f"starting_cash_usd={starting_cash} is below release minimum {minimum_cash}"
        )
    minimum_credit = float(compatibility.get("minimum_credit_usd", 0.0))
    if available_credit_usd < minimum_credit:
        raise InvalidDeployment(
            f"available credit {available_credit_usd} is below release minimum {minimum_credit}"
        )

    expected_decision = compatibility.get("decision_interval_seconds")
    if expected_decision is not None and float(expected_decision) != float(
        decision_interval_seconds
    ):
        raise InvalidDeployment(
            "release decision_interval_seconds is incompatible with this market process"
        )
    expected_product = compatibility.get("product_granularity_seconds")
    if expected_product is not None and float(expected_product) != float(
        product_granularity_seconds
    ):
        raise InvalidDeployment(
            "release product_granularity_seconds is incompatible with this market process"
        )


def assert_live_risk_contract(release: AgentRelease, limits: Any) -> None:
    """Require the process gateway to be at least as strict as the declared limits."""

    risk = dict(release.recipe.get("risk_limits") or {})
    checks = (
        ("max_open_orders", int(limits.max_open_orders)),
        ("max_new_orders_per_decision", int(limits.max_new_orders_per_decision)),
        ("credit_limit_usd", float(limits.credit_limit_usd)),
    )
    for field, enforced in checks:
        declared = float(risk[field])
        if enforced > declared:
            raise UnsafeLiveDeployment(
                f"live gateway {field}={enforced} is looser than release limit {declared}"
            )


def normalize_credential_bindings(release: AgentRelease, names: list[str]) -> list[str]:
    bindings = sorted(set(names))
    if any(_ENV_NAME.fullmatch(name) is None for name in bindings):
        raise InvalidDeployment("credential_bindings must contain environment variable names only")

    raw_llm = release.recipe.get("llm")
    llm: dict[str, Any] = raw_llm if isinstance(raw_llm, dict) else {}
    placeholder = llm.get("credential_env")
    if isinstance(placeholder, str):
        required = (
            placeholder.removeprefix("env://")
            if placeholder.startswith("env://")
            else placeholder.removeprefix("${").removesuffix("}")
        )
        if required not in bindings:
            raise InvalidDeployment(f"LLM deployment requires credential binding {required!r}")
    return bindings
