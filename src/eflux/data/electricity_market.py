"""External electricity-market price signals.

The first provider is CAISO OASIS. It returns zipped CSV/XML payloads; this
module keeps parsing to the Python standard library so the core app does not
gain another runtime dependency.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

CAISO_OASIS_URL = "https://oasis.caiso.com/oasisapi/SingleZip"
DEFAULT_REGION = "caiso_sp15"
DEFAULT_NODE = "TH_SP15_GEN-APND"
DEFAULT_FALLBACK_PRICE = Decimal("50")


@dataclass(frozen=True)
class ExternalMarketQuote:
    region: str
    node: str
    raw_lmp: Decimal
    p2p_anchor_price: Decimal
    import_price: Decimal
    export_price: Decimal
    interval_start: datetime | None
    interval_end: datetime | None
    currency: str
    unit: str
    status: str
    source: str
    detail: str
    fetched_at: datetime

    @property
    def external_trading_enabled(self) -> bool:
        return self.is_real_price

    @property
    def is_real_price(self) -> bool:
        return self.status in {"real", "fallback"}


def synthetic_quote(
    *,
    region: str = DEFAULT_REGION,
    node: str = DEFAULT_NODE,
    price: Decimal = DEFAULT_FALLBACK_PRICE,
    status: str = "synthetic",
    source: str = "Synthetic fallback",
    detail: str = "Using configured fallback price.",
    now: datetime | None = None,
    transaction_fee: Decimal = Decimal("0"),
) -> ExternalMarketQuote:
    now = now or datetime.now(UTC)
    anchor = _p2p_anchor(price)
    import_price, export_price = _import_export_prices(price, transaction_fee)
    return ExternalMarketQuote(
        region=region,
        node=node,
        raw_lmp=price,
        p2p_anchor_price=anchor,
        import_price=import_price,
        export_price=export_price,
        interval_start=None,
        interval_end=None,
        currency="USD",
        unit="$/MWh",
        status=status,
        source=source,
        detail=detail,
        fetched_at=now,
    )


def disabled_quote(
    *,
    region: str = DEFAULT_REGION,
    node: str = DEFAULT_NODE,
    fallback_price: Decimal = DEFAULT_FALLBACK_PRICE,
) -> ExternalMarketQuote:
    return synthetic_quote(
        region=region,
        node=node,
        price=fallback_price,
        status="disabled",
        source="External market disabled",
        detail="EFLUX_EXTERNAL_MARKET_ENABLED=false.",
    )


@dataclass(frozen=True)
class _PriceRow:
    interval_start: datetime
    interval_end: datetime
    price: Decimal
    source: str


class CaisoOasisClient:
    def __init__(
        self,
        *,
        base_url: str = CAISO_OASIS_URL,
        timeout_sec: float = 20.0,
    ) -> None:
        self.base_url = base_url
        self.timeout_sec = timeout_sec

    async def fetch_latest_quote(
        self,
        *,
        region: str = DEFAULT_REGION,
        node: str = DEFAULT_NODE,
        fallback_price: Decimal = DEFAULT_FALLBACK_PRICE,
        transaction_fee: Decimal = Decimal("0"),
        now: datetime | None = None,
    ) -> ExternalMarketQuote:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(minutes=15)
        detail_parts: list[str] = []

        # One client for both the RTM and DAM probes — a fresh connection pool
        # per poll is needless churn.
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            try:
                rtm = await self._fetch_rows(
                    client=client,
                    queryname="PRC_INTVL_LMP",
                    market_run_id="RTM",
                    version="2",
                    node=node,
                    start=cutoff - timedelta(hours=4),
                    end=cutoff,
                )
                row = _latest_before(rtm, cutoff)
                if row is not None:
                    return _quote_from_row(
                        row,
                        region=region,
                        node=node,
                        status="real",
                        fetched_at=now,
                        transaction_fee=transaction_fee,
                    )
                detail_parts.append("RTM returned no LMP rows")
            except Exception as exc:
                log.warning("CAISO RTM price fetch failed: %s", exc)
                detail_parts.append(f"RTM failed: {type(exc).__name__}")

            try:
                dam = await self._fetch_rows(
                    client=client,
                    queryname="PRC_LMP",
                    market_run_id="DAM",
                    version="12",
                    node=node,
                    start=cutoff - timedelta(hours=24),
                    end=cutoff + timedelta(hours=24),
                )
                row = _latest_before(dam, cutoff)
                if row is None and dam:
                    row = sorted(dam, key=lambda r: r.interval_start)[-1]
                if row is not None:
                    quote = _quote_from_row(
                        row,
                        region=region,
                        node=node,
                        status="fallback",
                        fetched_at=now,
                        transaction_fee=transaction_fee,
                    )
                    return replace(
                        quote,
                        detail=f"DAM fallback after {'; '.join(detail_parts) or 'RTM unavailable'}.",
                    )
                detail_parts.append("DAM returned no LMP rows")
            except Exception as exc:
                log.warning("CAISO DAM price fetch failed: %s", exc)
                detail_parts.append(f"DAM failed: {type(exc).__name__}")

        return synthetic_quote(
            region=region,
            node=node,
            price=fallback_price,
            detail="; ".join(detail_parts) or "CAISO OASIS unavailable.",
            now=now,
            transaction_fee=Decimal("0"),
        )

    async def _fetch_rows(
        self,
        *,
        client: httpx.AsyncClient,
        queryname: str,
        market_run_id: str,
        version: str,
        node: str,
        start: datetime,
        end: datetime,
    ) -> list[_PriceRow]:
        params = {
            "resultformat": "6",
            "queryname": queryname,
            "startdatetime": _oasis_ts(start),
            "enddatetime": _oasis_ts(end),
            "version": version,
            "market_run_id": market_run_id,
            "node": node,
        }
        resp = await client.get(self.base_url, params=params)
        resp.raise_for_status()
        payload = resp.content
        return parse_oasis_zip(payload, source=f"CAISO OASIS {market_run_id}")

    def fetch_lmp_history_sync(
        self,
        *,
        node: str = DEFAULT_NODE,
        start: datetime,
        end: datetime,
        market_run_id: str = "DAM",
        queryname: str = "PRC_LMP",
        version: str = "12",
        chunk_days: int = 7,
        pause_sec: float = 1.0,
    ) -> list[_PriceRow]:
        """Fetch a historical LMP series over [start, end] (UTC), chunked to respect OASIS
        per-request range caps. DAM/hourly by default (~720 rows for a month, a handful of
        requests). Synchronous — meant to run off the event loop (PPO training thread).
        Rows are deduped by interval_start and sorted ascending; failed chunks are skipped
        with a warning rather than aborting the whole fetch."""
        rows: list[_PriceRow] = []
        with httpx.Client(timeout=self.timeout_sec) as client:
            cur = start
            first = True
            while cur < end:
                chunk_end = min(end, cur + timedelta(days=chunk_days))
                if not first and pause_sec > 0:
                    time.sleep(pause_sec)  # be gentle with OASIS rate limits
                first = False
                params = {
                    "resultformat": "6",
                    "queryname": queryname,
                    "startdatetime": _oasis_ts(cur),
                    "enddatetime": _oasis_ts(chunk_end),
                    "version": version,
                    "market_run_id": market_run_id,
                    "node": node,
                }
                try:
                    resp = client.get(self.base_url, params=params)
                    resp.raise_for_status()
                    rows.extend(parse_oasis_zip(resp.content, source=f"CAISO OASIS {market_run_id}"))
                except Exception as exc:
                    log.warning("CAISO history chunk %s..%s failed: %s", cur, chunk_end, exc)
                cur = chunk_end
        by_start = {r.interval_start: r for r in rows}
        return [by_start[k] for k in sorted(by_start)]


def parse_oasis_zip(payload: bytes, *, source: str) -> list[_PriceRow]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        rows: list[_PriceRow] = []
        errors: list[str] = []
        for name in zf.namelist():
            text = zf.read(name).decode("utf-8", errors="replace")
            if name.lower().endswith(".csv"):
                rows.extend(_parse_csv(text, source=source))
            elif name.lower().endswith(".xml"):
                errors.extend(_parse_xml_errors(text))
        if rows:
            return rows
        if errors:
            log.info("CAISO OASIS returned no rows: %s", "; ".join(errors))
        return []


def _parse_csv(text: str, *, source: str) -> list[_PriceRow]:
    out: list[_PriceRow] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        lmp_type = (row.get("LMP_TYPE") or "").strip().upper()
        data_item = (row.get("XML_DATA_ITEM") or "").strip().upper()
        if lmp_type and lmp_type != "LMP":
            continue
        if data_item and data_item != "LMP_PRC":
            continue
        price_text = row.get("VALUE") or row.get("MW")
        start_text = row.get("INTERVALSTARTTIME_GMT")
        end_text = row.get("INTERVALENDTIME_GMT")
        if not price_text or not start_text or not end_text:
            continue
        try:
            out.append(
                _PriceRow(
                    interval_start=_parse_caiso_dt(start_text),
                    interval_end=_parse_caiso_dt(end_text),
                    price=Decimal(str(price_text)),
                    source=source,
                )
            )
        except Exception:
            continue
    return out


def _parse_xml_errors(text: str) -> list[str]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []
    errors: list[str] = []
    for elem in root.iter():
        if elem.tag.endswith("ERR_DESC") and elem.text:
            errors.append(elem.text.strip())
    return errors


def _quote_from_row(
    row: _PriceRow,
    *,
    region: str,
    node: str,
    status: str,
    fetched_at: datetime,
    transaction_fee: Decimal,
) -> ExternalMarketQuote:
    import_price, export_price = _import_export_prices(row.price, transaction_fee)
    return ExternalMarketQuote(
        region=region,
        node=node,
        raw_lmp=row.price,
        p2p_anchor_price=_p2p_anchor(row.price),
        import_price=import_price,
        export_price=export_price,
        interval_start=row.interval_start,
        interval_end=row.interval_end,
        currency="USD",
        unit="$/MWh",
        status=status,
        source=row.source,
        detail=f"{row.source} LMP for {row.interval_start.isoformat()}..{row.interval_end.isoformat()}.",
        fetched_at=fetched_at,
    )


def _latest_before(rows: list[_PriceRow], cutoff: datetime) -> _PriceRow | None:
    candidates = [r for r in rows if r.interval_end <= cutoff]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: r.interval_end)[-1]


def _p2p_anchor(price: Decimal) -> Decimal:
    return max(price, Decimal("0.01"))


def _import_export_prices(price: Decimal, transaction_fee: Decimal) -> tuple[Decimal, Decimal]:
    fee = max(transaction_fee, Decimal("0"))
    return price + fee, price - fee


def _oasis_ts(ts: datetime) -> str:
    ts = ts.astimezone(UTC).replace(second=0, microsecond=0)
    return ts.strftime("%Y%m%dT%H:%M-0000")


def _parse_caiso_dt(text: str) -> datetime:
    cleaned = text.replace("-00:00", "+00:00")
    return datetime.fromisoformat(cleaned).astimezone(UTC)
