#!/usr/bin/env bash
# EFlux one-shot launcher for macOS.
# - Starts backend + frontend as background processes (logs in .run/)
# - Waits for both to be ready (probes /health and /)
# - Opens the browser
#
# Stop everything: ./scripts/stop-all.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKEND_PORT=8000
FRONTEND_PORT=5173

RUN_DIR=".run"
mkdir -p "$RUN_DIR"

port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_http() {
  local url="$1" timeout="$2" label="$3"
  printf '  waiting for %s at %s ...' "$label" "$url"
  for ((i = 0; i < timeout; i++)); do
    if curl -fs -o /dev/null --max-time 2 "$url" 2>/dev/null; then
      printf ' ready (after %ds)\n' "$i"
      return 0
    fi
    printf '.'
    sleep 1
  done
  printf ' TIMEOUT\n'
  return 1
}

# --- Pre-flight ---

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "ERROR: venv not found at .venv/" >&2
  echo "  Run: uv sync --extra dev" >&2
  exit 1
fi

if [[ ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  echo "Frontend deps not installed. Running pnpm install first..."
  (cd frontend && pnpm install)
fi

# --- Backend ---

if port_listening "$BACKEND_PORT"; then
  echo "Backend already listening on :$BACKEND_PORT — skipping start"
else
  echo "Starting backend (log: $RUN_DIR/backend.log)..."
  nohup ./tasks.sh dev-stack >"$RUN_DIR/backend.log" 2>&1 &
  echo $! > "$RUN_DIR/backend.pid"
fi

if ! wait_http "http://127.0.0.1:$BACKEND_PORT/health" 30 backend; then
  echo "Backend failed to start. Tail $RUN_DIR/backend.log for errors." >&2
  exit 1
fi

# --- Frontend ---

if port_listening "$FRONTEND_PORT"; then
  echo "Frontend already listening on :$FRONTEND_PORT — skipping start"
else
  echo "Starting frontend (log: $RUN_DIR/frontend.log)..."
  nohup ./tasks.sh fe-dev >"$RUN_DIR/frontend.log" 2>&1 &
  echo $! > "$RUN_DIR/frontend.pid"
fi

if ! wait_http "http://127.0.0.1:$FRONTEND_PORT/" 90 frontend; then
  echo "Frontend failed to start. Tail $RUN_DIR/frontend.log for errors." >&2
  exit 1
fi

# --- Open browser ---

echo
echo "All up. Opening http://localhost:$FRONTEND_PORT/ ..."
open "http://localhost:$FRONTEND_PORT/"

echo
echo "To stop: ./scripts/stop-all.sh"
echo "Logs:    $RUN_DIR/backend.log  $RUN_DIR/frontend.log"
