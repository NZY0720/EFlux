from __future__ import annotations

from eflux.market.replay import replay_and_verify


def _rows():
    return [
        {
            "sequence_no": 1,
            "kind": "trade",
            "reference_id": "7",
            "payload": {"price": "50", "qty": "1"},
        },
        {
            "sequence_no": 2,
            "kind": "ledger.entry",
            "reference_id": "7",
            "payload": {"category": "trade", "amount_usd": "-0.050000"},
        },
        {
            "sequence_no": 3,
            "kind": "ledger.entry",
            "reference_id": "7",
            "payload": {"category": "trade", "amount_usd": "0.050000"},
        },
        {
            "sequence_no": 4,
            "kind": "delivery.settled",
            "reference_id": "p2p:interval",
            "payload": {
                "physical_net_injection_kwh": 0.75,
                "contracted_net_injection_kwh": 1.0,
                "imbalance_kwh": -0.25,
            },
        },
    ]


def test_replay_verifies_cash_and_energy_conservation():
    report = replay_and_verify(_rows())
    assert report.ok
    assert report.trade_count == 1
    assert report.delivery_count == 1


def test_replay_reports_sequence_cash_and_delivery_corruption():
    rows = _rows()
    rows[2]["sequence_no"] = 5
    rows[2]["payload"]["amount_usd"] = "0.040000"
    rows[3]["payload"]["imbalance_kwh"] = -0.20
    report = replay_and_verify(rows)
    assert not report.ok
    assert any("gap" in error for error in report.errors)
    assert any("conserve cash" in error for error in report.errors)
    assert any("physical-contract" in error for error in report.errors)
