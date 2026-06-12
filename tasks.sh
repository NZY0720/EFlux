#!/usr/bin/env bash
# Common dev tasks. Run as: ./tasks.sh <task>
# Tasks: help | start | stop | sync | run | dev | fe-install | fe-dev | smoke | ws | openapi | clean
#        migrate | makemigration | test | train-ppo

set -euo pipefail

cd "$(dirname "$0")"

export UV_PROJECT_ENVIRONMENT=.env
PY=".env/bin/python"

# macOS keeps re-adding UF_HIDDEN to everything under .env/ (the venv dir name
# starts with a dot, which Finder/Spotlight treats as hidden). Python 3.12's
# site.py refuses to process hidden .pth files for safety, which breaks the
# editable install (`import eflux` fails). Strip the flag on every invocation
# so the backend can always import its own package.
unhide_venv() {
  [[ -d .env ]] && chflags -R nohidden .env 2>/dev/null || true
}

# Run on every tasks.sh invocation — any subcommand that ends up importing
# eflux (run/dev/smoke/ws/openapi) needs the editable .pth file to be visible.
unhide_venv

task="${1:-help}"

case "$task" in
  help)
    cat <<'EOF'
Tasks: start | stop | dev | sync | run | smoke | ws | clean | openapi | fe-dev | fe-install
       migrate | makemigration | test
  start          - one-click: backend + frontend in background + open browser
  stop           - kill backend/frontend dev processes started by start
  sync           - uv sync all deps into .env/
  run            - start FastAPI server on :8000 (foreground)
  dev            - start FastAPI server with --reload
  fe-install     - install frontend deps (pnpm)
  fe-dev         - start Vite dev server on :5173
  smoke          - run REST smoke test (server must be running)
  ws             - run WebSocket smoke test
  openapi        - dump OpenAPI spec to docs/openapi.json
  clean          - delete dev sqlite db + pycache
  migrate        - apply pending alembic migrations (= alembic upgrade head)
  makemigration  - autogenerate a new alembic migration; usage: ./tasks.sh makemigration "<message>"
  test           - run pytest suite (tests/)
  train-ppo      - train PPO agent on the sandbox env (needs 'ai' extras: uv sync --extra ai)
EOF
    ;;

  sync)
    uv sync --extra dev
    unhide_venv   # uv recreates files inside .env/, which inherit UF_HIDDEN
    ;;

  run)
    exec "$PY" -m uvicorn eflux.api.main:app --host 127.0.0.1 --port 8000
    ;;

  dev)
    exec "$PY" -m uvicorn eflux.api.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir src
    ;;

  fe-install)
    cd frontend && pnpm install
    ;;

  fe-dev)
    cd frontend && exec pnpm exec vite --port 5173 --strictPort --host 127.0.0.1
    ;;

  smoke)
    base="http://127.0.0.1:8000"
    extract() { "$PY" -c "import json,sys;print(json.load(sys.stdin)$1)"; }

    dev_token=$(curl -fsS -X POST "$base/auth/magic-link" \
      -H 'Content-Type: application/json' \
      -d '{"email":"test@hku.hk"}' | extract "['dev_token']")

    session_json=$(curl -fsS -X POST "$base/auth/consume" \
      -H 'Content-Type: application/json' \
      -d "{\"token\":\"$dev_token\"}")
    session_token=$(printf '%s' "$session_json" | extract "['session_token']")
    user_id=$(printf '%s' "$session_json" | extract "['user_id']")

    # Unique name per run — VPP names collide (409) across runs on the same dev DB.
    vpp_json=$(curl -fsS -X POST "$base/vpps" \
      -H "Authorization: Bearer $session_token" \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"smoke-vpp-$(date +%s)\",\"params\":{}}")
    vpp_id=$(printf '%s' "$vpp_json" | extract "['id']")
    echo "user_id=$user_id vpp_id=$vpp_id"

    order_json=$(curl -fsS -X POST "$base/orders" \
      -H "Authorization: Bearer $session_token" \
      -H 'Content-Type: application/json' \
      -d "{\"vpp_id\":\"$vpp_id\",\"side\":\"buy\",\"price\":80,\"qty\":0.05}")
    order_id=$(printf '%s' "$order_json" | extract "['order_id']")
    trades=$(printf '%s' "$order_json" | extract "['trades'].__len__()")
    remaining=$(printf '%s' "$order_json" | extract "['remaining_qty']")
    echo "order_id=$order_id trades=$trades remaining=$remaining"

    curl -fsS "$base/market/snapshot?depth=5" | "$PY" -m json.tool
    ;;

  ws)
    exec "$PY" scripts/ws_smoke.py
    ;;

  openapi)
    mkdir -p docs
    "$PY" -m eflux.cli openapi > docs/openapi.json
    echo "wrote docs/openapi.json"
    ;;

  clean)
    rm -f eflux_dev.db eflux_dev.db-journal
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
    echo "cleaned dev db + pycache"
    ;;

  start)
    exec ./scripts/start-all.sh
    ;;

  stop)
    exec ./scripts/stop-all.sh
    ;;

  migrate)
    exec .env/bin/alembic upgrade head
    ;;

  makemigration)
    msg="${2:-}"
    if [[ -z "$msg" ]]; then
      echo "usage: ./tasks.sh makemigration \"<message>\"" >&2
      exit 1
    fi
    exec .env/bin/alembic revision --autogenerate -m "$msg"
    ;;

  test)
    shift  # drop "test", forward the rest to pytest
    exec "$PY" -m pytest tests "$@"
    ;;

  train-ppo)
    shift  # drop "train-ppo", forward the rest to the trainer
    exec "$PY" -m eflux.agents.ppo.train "$@"
    ;;

  *)
    echo "Unknown task: $task. Run './tasks.sh help' for options." >&2
    exit 1
    ;;
esac
