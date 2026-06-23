#!/usr/bin/env bash
# Double-click shutdown for macOS Finder — stops the EFlux backend (:8000) and
# frontend (:5173) on this device. Mirror of "Start EFlux.command".

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "Stopping EFlux from:"
echo "  $PROJECT_ROOT"
echo

./scripts/stop-all.sh

echo
echo "EFlux stop command finished."
echo "You can close this Terminal window."
