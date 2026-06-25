#!/usr/bin/env bash
# Double-click launcher for macOS Finder — starts the REAL-TIME PRICE MARKET.
#
# Pure price-taking against the live CAISO price: every order settles against the
# grid at import/export (lmp ± fee). Agents never trade each other and their volume
# never moves the price — a clean testbed for strategy P&L against a real price curve.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

export EFLUX_MARKET_MODE=realprice
export EFLUX_SCENARIO_FILE=scenarios/realprice.yaml

echo "Starting EFlux — REAL-TIME PRICE MARKET — from:"
echo "  $PROJECT_ROOT"
echo

# One market runs at a time; stop any market already running so the mode we just
# picked is the one that actually comes up (start-all.sh skips an already-busy port).
./scripts/stop-all.sh >/dev/null 2>&1 || true

./scripts/start-all.sh

echo
echo "EFlux (real-time price market) launch command finished."
echo "You can close this Terminal window after the browser opens."
