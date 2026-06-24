from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from eflux.data.electricity_market import CaisoOasisClient, _PriceRow, parse_oasis_zip


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


def test_parse_oasis_rtm_zip_filters_lmp_rows():
    payload = _zip(
        "rtm.csv",
        "\n".join(
            [
                "INTERVALSTARTTIME_GMT,INTERVALENDTIME_GMT,MARKET_RUN_ID,LMP_TYPE,XML_DATA_ITEM,VALUE",
                "2026-06-24T04:00:00-00:00,2026-06-24T04:05:00-00:00,RTM,MCC,LMP_CONG_PRC,-0.1",
                "2026-06-24T04:00:00-00:00,2026-06-24T04:05:00-00:00,RTM,LMP,LMP_PRC,31.25",
            ]
        ),
    )

    rows = parse_oasis_zip(payload, source="CAISO OASIS RTM")

    assert len(rows) == 1
    assert rows[0].price == Decimal("31.25")
    assert rows[0].interval_end == datetime(2026, 6, 24, 4, 5, tzinfo=UTC)


def test_parse_oasis_error_xml_returns_no_rows():
    payload = _zip(
        "INVALID_REQUEST.xml",
        """<?xml version="1.0"?><OASISReport><ERROR><ERR_DESC>No data returned</ERR_DESC></ERROR></OASISReport>""",
    )

    assert parse_oasis_zip(payload, source="CAISO OASIS RTM") == []


@pytest.mark.asyncio
async def test_caiso_client_falls_back_to_dam(monkeypatch):
    now = datetime(2026, 6, 24, 6, 0, tzinfo=UTC)
    dam_row = _PriceRow(
        interval_start=now - timedelta(hours=1),
        interval_end=now,
        price=Decimal("42.5"),
        source="CAISO OASIS DAM",
    )
    client = CaisoOasisClient()

    async def fake_fetch_rows(*, market_run_id: str, **_kwargs):
        return [] if market_run_id == "RTM" else [dam_row]

    monkeypatch.setattr(client, "_fetch_rows", fake_fetch_rows)

    quote = await client.fetch_latest_quote(now=now, transaction_fee=Decimal("2"))

    assert quote.status == "fallback"
    assert quote.source == "CAISO OASIS DAM"
    assert quote.raw_lmp == Decimal("42.5")
    assert quote.import_price == Decimal("44.5")
    assert quote.export_price == Decimal("40.5")


@pytest.mark.asyncio
async def test_caiso_client_synthetic_when_all_sources_fail(monkeypatch):
    client = CaisoOasisClient()

    async def fake_fetch_rows(**_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_fetch_rows", fake_fetch_rows)

    quote = await client.fetch_latest_quote(now=datetime(2026, 6, 24, 6, 0, tzinfo=UTC))

    assert quote.status == "synthetic"
    assert quote.raw_lmp == Decimal("50")
    assert quote.p2p_anchor_price == Decimal("50")
