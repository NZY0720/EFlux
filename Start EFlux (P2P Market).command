#!/usr/bin/env bash
# Double-click launcher for macOS Finder — starts the P2P MARKET.
#
# Peer-to-peer continuous double auction: agents trade only with each other.
# CAISO is shown as a reference line but never settles trades or anchors prices.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

export EFLUX_MARKET_MODE=p2p
export EFLUX_SCENARIO_FILE=scenarios/p2p.yaml

echo "Starting EFlux — P2P MARKET — from:"
echo "  $PROJECT_ROOT"
echo

# One market runs at a time; stop any market already running so the mode we just
# picked is the one that actually comes up (start-all.sh skips an already-busy port).
./scripts/stop-all.sh >/dev/null 2>&1 || true

./scripts/start-all.sh

echo
echo "EFlux (P2P market) launch command finished."
echo "You can close this Terminal window after the browser opens."
