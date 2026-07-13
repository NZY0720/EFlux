"""`eflux` CLI — primary entry point."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click


@click.group()
def main() -> None:
    """EFlux — VPP electricity trading platform."""


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--reload/--no-reload", default=False, show_default=True)
def run(host: str, port: int, reload: bool) -> None:
    """Start the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "eflux.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@main.command()
def openapi() -> None:
    """Dump OpenAPI spec to stdout (JSON)."""
    import json

    from eflux.api.main import create_app

    app = create_app()
    click.echo(json.dumps(app.openapi(), indent=2))


@main.command(name="agent-spec-schema")
def agent_spec_schema() -> None:
    """Dump the AgentSpec JSON Schema to stdout — the contract a market
    participant (built-in roster entry or external VPP) is declared with."""
    import json

    from eflux.simulator.agent_spec import agent_spec_json_schema

    click.echo(json.dumps(agent_spec_json_schema(), indent=2))


@main.group()
def scenario() -> None:
    """Validate and fingerprint versioned ScenarioSpec files."""


@scenario.command(name="validate")
@click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def scenario_validate(path: Path) -> None:
    from eflux.simulator.scenario_spec import load_scenario_spec

    spec = load_scenario_spec(path)
    click.echo(
        f"valid ScenarioSpec v{spec.schema_version}: {spec.name} "
        f"({len(spec.participants)} participants)"
    )


@scenario.command(name="inspect")
@click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def scenario_inspect(path: Path) -> None:
    import json

    from eflux.simulator.scenario_spec import load_scenario_spec

    spec = load_scenario_spec(path)
    click.echo(
        json.dumps(
            {
                "name": spec.name,
                "schema_version": spec.schema_version,
                "market_mode": spec.market_mode,
                "participants": len(spec.participants),
                "scenario_sha256": spec.semantic_sha256,
            },
            indent=2,
        )
    )


@scenario.command(name="hash")
@click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def scenario_hash(path: Path) -> None:
    from eflux.simulator.scenario_spec import load_scenario_spec

    click.echo(load_scenario_spec(path).semantic_sha256)


@scenario.command(name="normalize")
@click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
def scenario_normalize(path: Path, output: Path | None) -> None:
    from eflux.simulator.scenario_spec import load_scenario_spec, normalized_scenario_yaml

    rendered = normalized_scenario_yaml(load_scenario_spec(path))
    if output is None:
        click.echo(rendered, nl=False)
    else:
        output.write_text(rendered, encoding="utf-8")
        click.echo(str(output))


@main.command(name="compare")
@click.argument("left", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.argument("right", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False))
def compare_runs(left: Path, right: Path, output: Path | None) -> None:
    """Compare two backtest run directories without inventing significance."""
    import json

    from eflux.backtest.compare import compare_backtest_runs

    rendered = json.dumps(compare_backtest_runs(left, right), indent=2)
    if output is None:
        click.echo(rendered)
    else:
        output.write_text(rendered + "\n", encoding="utf-8")
        click.echo(str(output))


@main.command()
def info() -> None:
    """Print runtime info."""
    from eflux import __version__
    from eflux.config import get_settings

    s = get_settings()
    click.echo(f"EFlux v{__version__}")
    click.echo(f"  env:          {s.env}")
    click.echo(f"  db_url:       {s.db_url}")
    click.echo(f"  redis_url:    {s.redis_url}")
    click.echo(f"  market_speed: {s.market_speed}x")
    click.echo(f"  llm_provider: {s.llm_provider} (key present: {s.llm_api_key is not None})")


def _parse_date_option(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@main.command()
@click.option("--market-mode", type=click.Choice(["p2p", "realprice"]), default="p2p", show_default=True)
@click.option("--scenario", type=Path, default=None, help="Override the per-market backtest roster.")
@click.option("--months", type=int, default=1, show_default=True)
@click.option("--tick-seconds", type=float, default=1.0, show_default=True)
@click.option("--llm-cadence-hours", type=float, default=1.0, show_default=True)
@click.option("--llm-mode", type=click.Choice(["live-strict"]), default="live-strict", show_default=True)
@click.option("--out-dir", type=Path, default=None, help="Backtest artifact directory.")
@click.option("--start-date", default=None, help="UTC date/datetime, e.g. 2026-05-01.")
@click.option("--end-date", default=None, help="UTC date/datetime, e.g. 2026-06-01.")
@click.option("--max-ticks", type=int, default=None, help="Debug/smoke cap; omit for the full window.")
@click.option("--skip-ppo-train", is_flag=True, help="Use roster checkpoints instead of training a temp backtest checkpoint.")
@click.option("--skip-real-data", is_flag=True, help="Use flat synthetic price for smoke tests.")
def backtest(
    market_mode: str,
    scenario: Path | None,
    months: int,
    tick_seconds: float,
    llm_cadence_hours: float,
    llm_mode: str,
    out_dir: Path | None,
    start_date: str | None,
    end_date: str | None,
    max_ticks: int | None,
    skip_ppo_train: bool,
    skip_real_data: bool,
) -> None:
    """Run a strict, headless historical backtest and save CSV/SVG artifacts."""
    from eflux.backtest import BacktestConfig, run_backtest
    from eflux.config import PROJECT_ROOT

    config = BacktestConfig(
        market_mode=market_mode,  # type: ignore[arg-type]
        scenario=scenario,
        months=months,
        tick_seconds=tick_seconds,
        llm_cadence_hours=llm_cadence_hours,
        llm_mode=llm_mode,  # type: ignore[arg-type]
        out_dir=out_dir or (PROJECT_ROOT / "artifacts" / "backtests"),
        start=_parse_date_option(start_date),
        end=_parse_date_option(end_date),
        max_ticks=max_ticks,
        train_ppo=not skip_ppo_train,
        fetch_real_data=not skip_real_data,
    )
    result = run_backtest(config)
    click.echo(f"backtest complete: {result.run_dir}")
    click.echo(f"  ticks_run: {result.ticks_run}")
    click.echo(f"  llm_calls: {result.llm_calls}")
    click.echo(f"  participants: {result.participant_count}")


if __name__ == "__main__":
    main()
