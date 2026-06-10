#!/usr/bin/env bash
# Double-click launcher for macOS Finder.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "Starting EFlux from:"
echo "  $PROJECT_ROOT"
echo

./scripts/start-all.sh

echo
echo "EFlux launch command finished."
echo "You can close this Terminal window after the browser opens."
