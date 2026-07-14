from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from eflux.api.routers.vpps import ManagedVPPUpdate, update_managed_vpp
from eflux.bridge.bus import InMemoryBus
from eflux.db.models import VPP, User
from eflux.market.ledger import LedgerCategory
from eflux.simulator.agent_spec import validate_vpp_params
from eflux.simulator.runner import Simulator
from eflux.simulator.scenarios import provision_managed_vpp

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


async def _managed_vpp_with_unsettled_contract(db_session, monkeypatch):
    monkeypatch.setattr("eflux.simulator.scenarios._build_executor", lambda *args, **kwargs: None)
    user = User(email="managed-update@example.com")
    db_session.add(user)
    await db_session.flush()
    params = validate_vpp_params(
        {
            "pv_kw_peak": 0,
            "battery_kwh": 10,
            "battery_kw_max": 3,
            "load_kw_base": 0,
        }
    )
    row = VPP(
        owner_id=user.id,
        name="managed-update",
        params=params,
        is_external=True,
        is_managed=True,
        managed_config={
            "persona": "old persona",
            "agent_params": {},
            "seed": 11,
            "model": None,
            "algorithm": "ppo",
            "llm_enabled": True,
            "online_learning": False,
        },
    )
    db_session.add(row)
    await db_session.flush()

    sim = Simulator(InMemoryBus(), sim_epoch=NOW)
    vpp = provision_managed_vpp(
        sim,
        owner_id=user.id,
        name=row.name,
        params=params,
        persona_prompt="old persona",
        seed=11,
        managed_def_id=row.id,
        algorithm="ppo",
        llm_enabled=True,
        online_learning=False,
        use_real_weather=False,
    )
    interval = sim._ensure_products(NOW)[0]
    runtime = sim.gateway.participants[vpp.vpp_id]
    runtime.position(interval).contracted_sell_kwh = 0.25
    vpp.battery.soc_kwh = 4.25
    vpp.state.soc_kwh = 4.25
    sim.gateway.ledger.post(
        participant_id=vpp.vpp_id,
        category=LedgerCategory.INITIAL_INVENTORY,
        amount_usd=Decimal("7.25"),
        occurred_at=NOW,
    )
    vpp.state.pnl = sim.gateway.ledger.balance(vpp.vpp_id)
    return user, row, sim, vpp, runtime, interval


@pytest.mark.asyncio
async def test_persona_only_update_swaps_brain_with_unsettled_contract_in_place(
    db_session, monkeypatch
):
    user, row, sim, vpp, runtime, interval = await _managed_vpp_with_unsettled_contract(
        db_session, monkeypatch
    )
    participant_id = vpp.vpp_id
    battery = vpp.battery
    old_agent = vpp.agent
    roster_ids = set(sim.vpps)
    next_vpp_id = sim._next_vpp_id

    result = await update_managed_vpp(
        row.id,
        ManagedVPPUpdate(persona="new cockpit persona"),
        db_session,
        user,
        sim,
    )

    assert result.vpp_id == participant_id
    assert sim.vpps[participant_id] is vpp
    assert vpp.agent is not old_agent
    assert vpp.agent.persona_prompt == "new cockpit persona"
    assert sim.gateway.participants[participant_id] is runtime
    assert vpp.battery is battery
    assert vpp.battery.soc_kwh == pytest.approx(4.25)
    assert runtime.position(interval).contracted_sell_kwh == pytest.approx(0.25)
    assert sim.gateway.ledger.balance(participant_id) == Decimal("7.250000")
    assert vpp.state.pnl == Decimal("7.250000")
    assert set(sim.vpps) == roster_ids
    assert sim._next_vpp_id == next_vpp_id


@pytest.mark.asyncio
async def test_physical_update_with_unsettled_contract_still_returns_409(
    db_session, monkeypatch
):
    user, row, sim, vpp, runtime, _interval = await _managed_vpp_with_unsettled_contract(
        db_session, monkeypatch
    )
    participant_id = vpp.vpp_id
    battery = vpp.battery
    agent = vpp.agent
    roster_ids = set(sim.vpps)

    with pytest.raises(HTTPException) as exc_info:
        await update_managed_vpp(
            row.id,
            ManagedVPPUpdate(params={"battery_kwh": 12}),
            db_session,
            user,
            sim,
        )

    assert exc_info.value.status_code == 409
    assert sim.vpps[participant_id] is vpp
    assert sim.gateway.participants[participant_id] is runtime
    assert vpp.agent is agent
    assert vpp.battery is battery
    assert vpp.battery.soc_kwh == pytest.approx(4.25)
    assert sim.gateway.ledger.balance(participant_id) == Decimal("7.250000")
    assert set(sim.vpps) == roster_ids
