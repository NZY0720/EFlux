"""Benchmarks — read-only API over the backtest runner's artifacts.

Surfaces artifacts/backtests/<run>/ (manifest.json, participant/group metrics CSVs,
SVG charts) so reproducible offline runs are visible in the product, not just on the
operator's disk. Strictly read-only and path-guarded: run ids and chart filenames are
validated against tight patterns AND resolved paths are containment-checked, so no
request can escape the artifacts directory.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from eflux.backtest.compare import compare_backtest_runs
from eflux.config import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])

# Matches backtest.runner._new_run_dir output, e.g. "20260628T171253Z-p2p".
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-(p2p|realprice)$")
_CHART_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.svg$")


class BenchmarkSummary(BaseModel):
    run_id: str
    market_mode: str
    status: str  # "ok" | "failed" | "incomplete" (no status stamped — aborted/smoke run)
    start: str | None = None
    end: str | None = None
    months: int | None = None
    tick_seconds: float | None = None
    llm_mode: str | None = None
    llm_calls: int | None = None
    expected_llm_calls: int | None = None
    ticks_run: int | None = None
    live_participants: int | None = None
    finished_at: str | None = None
    charts: list[str]


class BenchmarkDetail(BaseModel):
    run_id: str
    manifest: dict
    participants: list[dict]
    groups: list[dict]
    charts: list[str]


def _artifacts_base() -> Path:
    base = Path(get_settings().backtest_artifacts_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return base


def _run_dir(run_id: str) -> Path:
    """Resolve a validated run directory. Two independent guards against traversal:
    the run-id pattern, and containment of the resolved path in the artifacts base."""
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "benchmark run not found")
    base = _artifacts_base().resolve()
    path = (base / run_id).resolve()
    if not path.is_relative_to(base) or not path.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "benchmark run not found")
    return path


def _charts(run_dir: Path) -> list[str]:
    return sorted(p.name for p in run_dir.glob("*.svg") if _CHART_RE.match(p.name))


def _coerce(value: str):
    """CSV cell → typed value (int/float/bool where they parse, else the string)."""
    if value in ("True", "False"):
        return value == "True"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            return [
                {k: _coerce(v) for k, v in row.items() if k is not None}
                for row in csv.DictReader(f)
            ]
    except (OSError, csv.Error):
        log.exception("Failed to read benchmark CSV %s", path)
        return []


@router.get("", response_model=list[BenchmarkSummary])
async def list_benchmarks() -> list[BenchmarkSummary]:
    """All recorded backtest runs, newest first. Directories without a readable
    manifest (in-flight runs, stray files like logs/) are skipped."""
    base = _artifacts_base()
    if not base.is_dir():
        return []
    out: list[BenchmarkSummary] = []
    for d in sorted(base.iterdir(), reverse=True):  # run ids sort by timestamp
        if not d.is_dir() or not _RUN_ID_RE.match(d.name):
            continue
        try:
            manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            BenchmarkSummary(
                run_id=d.name,
                market_mode=str(manifest.get("market_mode", "")),
                status=str(manifest.get("status", "incomplete")),
                start=manifest.get("start"),
                end=manifest.get("end"),
                months=manifest.get("months"),
                tick_seconds=manifest.get("tick_seconds"),
                llm_mode=manifest.get("llm_mode"),
                llm_calls=manifest.get("llm_calls"),
                expected_llm_calls=manifest.get("expected_llm_calls"),
                ticks_run=manifest.get("ticks_run"),
                live_participants=manifest.get("live_participants"),
                finished_at=manifest.get("finished_at"),
                charts=_charts(d),
            )
        )
    return out


@router.get("/compare")
async def compare_benchmarks(left: str, right: str) -> dict:
    """Return a transparent right-minus-left report for two artifact runs."""

    return compare_backtest_runs(_run_dir(left), _run_dir(right))


@router.get("/{run_id}", response_model=BenchmarkDetail)
async def benchmark_detail(run_id: str) -> BenchmarkDetail:
    """Full manifest + parsed participant/group metrics + available charts."""
    run_dir = _run_dir(run_id)
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "benchmark run has no manifest") from e
    return BenchmarkDetail(
        run_id=run_id,
        manifest=manifest,
        participants=_read_csv(run_dir / "participant_metrics.csv"),
        groups=_read_csv(run_dir / "group_metrics.csv"),
        charts=_charts(run_dir),
    )


@router.get("/{run_id}/charts/{filename}")
async def benchmark_chart(run_id: str, filename: str) -> FileResponse:
    run_dir = _run_dir(run_id)
    if not _CHART_RE.match(filename):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chart not found")
    path = (run_dir / filename).resolve()
    if not path.is_relative_to(run_dir) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chart not found")
    return FileResponse(path, media_type="image/svg+xml")
