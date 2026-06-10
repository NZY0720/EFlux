"""`eflux` CLI — primary entry point."""

from __future__ import annotations

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


if __name__ == "__main__":
    main()
