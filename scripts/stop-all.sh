#!/usr/bin/env bash
# Stop backend/frontend processes started by start-all.sh.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

RUN_DIR=".run"
BACKEND_PORT=8000
FRONTEND_PORT=5173

killed=0

kill_pid_file() {
  local pid_file="$1" label="$2"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid=$(cat "$pid_file")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $label (pid=$pid)"
      # Kill whole process group so uvicorn/vite child processes also exit.
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      killed=$((killed + 1))
    fi
    rm -f "$pid_file"
  fi
}

kill_port_fallback() {
  local port="$1" label="$2"
  local pids
  pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "Stopping leftover $label on :$port (pids=$pids)"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)
    [[ -n "$pids" ]] && echo "$pids" | xargs kill -9 2>/dev/null || true
    killed=$((killed + 1))
  fi
}

kill_pid_file "$RUN_DIR/backend.pid" backend
kill_pid_file "$RUN_DIR/frontend.pid" frontend
kill_pid_file "$RUN_DIR/eval-worker.pid" "evaluation worker"
kill_pid_file "$RUN_DIR/ecosystem-worker.pid" "ecosystem worker"

# Give the killed processes a moment to release their listening sockets before
# the port fallback below, otherwise we'd double-count them.
sleep 1

# Catch anything still bound to the dev ports (orphans or processes started outside start-all.sh).
kill_port_fallback "$BACKEND_PORT" backend
kill_port_fallback "$FRONTEND_PORT" frontend

if (( killed == 0 )); then
  echo "Nothing to stop."
else
  echo "Stopped $killed process(es)."
fi
