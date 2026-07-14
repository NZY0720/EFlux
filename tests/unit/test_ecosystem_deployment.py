from types import SimpleNamespace

import pytest

from eflux.ecosystem.deployment import (
    InvalidDeployment,
    UnsafeLiveDeployment,
    assert_deployment_compatibility,
    assert_live_risk_contract,
    normalize_credential_bindings,
)


def _release(**overrides):
    values = {
        "market": "p2p",
        "compatibility": {
            "market": "p2p",
            "profile_id": "battery-only",
            "battery_kwh_range": [5, 25],
            "minimum_cash_usd": 10,
            "minimum_credit_usd": 20,
            "decision_interval_seconds": 30,
            "product_granularity_seconds": 300,
        },
        "recipe": {
            "risk_limits": {
                "max_open_orders": 50,
                "max_new_orders_per_decision": 10,
                "credit_limit_usd": 100,
            },
            "llm": {"credential_env": "env://MODEL_API_KEY"},
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_deployment_contract_accepts_matching_profile_and_process() -> None:
    assert_deployment_compatibility(
        _release(),
        profile={"spec": {"asset_type": "battery"}},
        profile_id="battery-only",
        params={"battery_kwh": 20, "starting_cash_usd": 20},
        available_credit_usd=120,
        decision_interval_seconds=30,
        product_granularity_seconds=300,
    )


def test_deployment_contract_rejects_out_of_range_endowment() -> None:
    with pytest.raises(InvalidDeployment, match="battery_kwh"):
        assert_deployment_compatibility(
            _release(),
            profile={"spec": {"asset_type": "battery"}},
            profile_id="battery-only",
            params={"battery_kwh": 30, "starting_cash_usd": 20},
            available_credit_usd=120,
            decision_interval_seconds=30,
            product_granularity_seconds=300,
        )


def test_live_gateway_must_not_be_looser_than_release() -> None:
    limits = SimpleNamespace(
        max_open_orders=51,
        max_new_orders_per_decision=10,
        credit_limit_usd=100,
    )
    with pytest.raises(UnsafeLiveDeployment, match="max_open_orders"):
        assert_live_risk_contract(_release(), limits)


def test_credentials_are_names_only_and_required_placeholder_is_bound() -> None:
    assert normalize_credential_bindings(
        _release(), ["MODEL_API_KEY", "MODEL_API_KEY"]
    ) == ["MODEL_API_KEY"]
    with pytest.raises(InvalidDeployment, match="requires credential binding"):
        normalize_credential_bindings(_release(), [])
    with pytest.raises(InvalidDeployment, match="environment variable names"):
        normalize_credential_bindings(_release(), ["MODEL_API_KEY=secret"])
