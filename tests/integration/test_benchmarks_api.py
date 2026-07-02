"""Integration tests for the /benchmarks artifact API (list, detail, chart serving,
and — critically — path-traversal rejection)."""

from __future__ import annotations

import json

import pytest

from eflux.config import get_settings

pytestmark = pytest.mark.asyncio

RUN_ID = "20260701T120000Z-p2p"
MANIFEST = {
    "market_mode": "p2p",
    "months": 1,
    "start": "2026-05-26T00:00:00+00:00",
    "end": "2026-06-25T00:00:00+00:00",
    "tick_seconds": 60.0,
    "llm_mode": "live-strict",
    "expected_llm_calls": 8,
    "llm_calls": 8,
    "ticks_run": 1000,
    "live_participants": 4,
    "status": "ok",
    "finished_at": "2026-07-01T13:00:00+00:00",
}
PARTICIPANTS_CSV = (
    "vpp_id,name,strategy,is_llm,mirror_of,group_id,realized_pnl,mark_to_market,"
    "energy_bought_kwh,energy_sold_kwh,trade_count,risk_rejections,"
    "unresolved_imbalance_kwh,final_soc_frac\n"
    "-31,gas-a,TruthfulAgent,False,,g1,603189.49,603189.49,0.0,9732.9,54639,0,0.0,0.0\n"
    "-32,llm-a,HybridPolicyAgent,True,,g2,-812.5,-800.25,55.0,12.0,120,2,1.5,0.4\n"
)
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'


@pytest.fixture
def artifacts_dir(tmp_path, monkeypatch):
    run = tmp_path / RUN_ID
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (run / "participant_metrics.csv").write_text(PARTICIPANTS_CSV, encoding="utf-8")
    (run / "overview_leaderboard.svg").write_text(SVG, encoding="utf-8")
    # Noise the lister must skip: a non-run dir, an in-flight run without manifest.
    (tmp_path / "logs").mkdir()
    (tmp_path / "20260701T130000Z-p2p").mkdir()
    # A secret OUTSIDE the artifacts base — traversal attempts aim for this.
    (tmp_path.parent / "secret.svg").write_text("top secret", encoding="utf-8")
    monkeypatch.setenv("EFLUX_BACKTEST_ARTIFACTS_DIR", str(tmp_path))
    # The app may already have booted (client fixture) and cached Settings; the
    # router reads get_settings() per request, so clearing here is sufficient.
    get_settings.cache_clear()
    return tmp_path


async def test_list_and_detail_roundtrip(client, artifacts_dir):
    r = await client.get("/benchmarks")
    assert r.status_code == 200
    runs = r.json()
    assert [x["run_id"] for x in runs] == [RUN_ID]  # noise dirs skipped
    assert runs[0]["status"] == "ok"
    # LLM integrity: calls matched the pre-flight expectation.
    assert runs[0]["llm_calls"] == runs[0]["expected_llm_calls"] == 8
    assert runs[0]["charts"] == ["overview_leaderboard.svg"]

    r = await client.get(f"/benchmarks/{RUN_ID}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["manifest"]["ticks_run"] == 1000
    rows = {p["name"]: p for p in detail["participants"]}
    assert rows["gas-a"]["realized_pnl"] == pytest.approx(603189.49)
    assert rows["llm-a"]["is_llm"] is True  # CSV "True" coerced to bool
    assert rows["gas-a"]["trade_count"] == 54639
    assert detail["groups"] == []  # missing CSV degrades to empty, not an error

    r = await client.get(f"/benchmarks/{RUN_ID}/charts/overview_leaderboard.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text


async def test_traversal_and_bad_ids_rejected(client, artifacts_dir):
    # Literal "../" segments are collapsed by URL normalization before routing, so the
    # vectors that actually reach the handler are encoded dots and pattern-invalid ids.
    # Run ids failing the runner's naming pattern are 404 before any FS touch.
    for bad in ("%2e%2e", "%2e%2e%2f%2e%2e", "logs", "x" * 200, "20990101T000000Z-p2p"):
        r = await client.get(f"/benchmarks/{bad}")
        assert r.status_code in (404, 422), bad
    # Chart filename traversal: encoded slash decodes into the path param but the
    # filename pattern (no "/") rejects it; wrong extensions rejected too.
    for bad in ("..%2Fmanifest.json", "%2e%2e%2fmanifest.json", "a.png", "manifest.json"):
        r = await client.get(f"/benchmarks/{RUN_ID}/charts/{bad}")
        assert r.status_code in (404, 422), bad
    # And a well-formed request for a file that exists only OUTSIDE the base: 404.
    r = await client.get(f"/benchmarks/{RUN_ID}/charts/secret.svg")
    assert r.status_code == 404


async def test_missing_artifacts_dir_is_empty_list(client, monkeypatch, tmp_path):
    monkeypatch.setenv("EFLUX_BACKTEST_ARTIFACTS_DIR", str(tmp_path / "nowhere"))
    get_settings.cache_clear()
    r = await client.get("/benchmarks")
    assert r.status_code == 200
    assert r.json() == []
