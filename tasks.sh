#!/usr/bin/env bash
# Common dev tasks. Run as: ./tasks.sh <task>
# Tasks: help | start | stop | sync | api | dev-stack | dev | fe-install | fe-dev | smoke | ws
#        contracts | clean | migrate | makemigration | test | verify | train-ppo | backtest
#        eval-worker | ecosystem-worker

set -euo pipefail

cd "$(dirname "$0")"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src"
PY=".venv/bin/python"

task="${1:-help}"

case "$task" in
  help)
    cat <<'EOF'
Tasks: start | stop | dev | sync | api | dev-stack | smoke | ws | clean | contracts | fe-dev | fe-install
       migrate | makemigration | test | verify | train-ppo | bench | eval-ppo | backtest | eval-worker | ecosystem-worker
  start          - one-click: backend + frontend in background + open browser
  stop           - kill backend/frontend dev processes started by start
  sync           - sync all dependencies into .venv/
  api            - start only the FastAPI server on :8000 (foreground)
  run            - compatibility alias for api
  dev-stack      - start API plus both local workers
  dev            - start FastAPI server with --reload
  fe-install     - install frontend deps (pnpm)
  fe-dev         - start Vite dev server on :5173
  smoke          - run REST smoke test (server must be running)
  ws             - run WebSocket smoke test
  contracts      - regenerate checked-in OpenAPI, agent schema and TypeScript contracts
  clean          - delete dev sqlite db + pycache
  migrate        - apply pending alembic migrations (= alembic upgrade head)
  makemigration  - autogenerate a new alembic migration; usage: ./tasks.sh makemigration "<message>"
  test           - run pytest suite (tests/)
  verify         - run the complete local release gate
  train-ppo      - train the live torch PPO policy (BC warm-start; needs 'ai' extras). Per market:
#                    train-ppo --real-data --market-mode p2p       --out checkpoints/bc_primitive_p2p_v1.pt
#                    train-ppo --real-data --market-mode realprice --out checkpoints/bc_primitive_realprice_grid_v1.pt
  bench          - score candidate agents vs a fixed counter-roster (leaderboard)
  eval-ppo       - score a trained torch checkpoint vs the benchmark baselines (--checkpoint FILE.pt)
  backtest       - run a strict historical backtest (default: 1 month, 1s ticks, hourly live LLM)
  eval-worker    - run the official evaluation queue worker (--once for one queued run)
  ecosystem-worker - run Agent Release / Dataset jobs (--once for one queued job)
EOF
    ;;

  sync)
    uv sync --extra dev
    ;;

  api|run)
    exec "$PY" -m uvicorn eflux.api.main:app --host 127.0.0.1 --port 8000
    ;;

  dev-stack)
    mkdir -p .run
    "$PY" -m eflux.evaluation.worker >.run/eval-worker.log 2>&1 &
    echo $! > .run/eval-worker.pid
    "$PY" -m eflux.ecosystem.worker >.run/ecosystem-worker.log 2>&1 &
    echo $! > .run/ecosystem-worker.pid
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
    product_id=$(curl -fsS "$base/market/products" | extract "[0]['product_id']")
    echo "user_id=$user_id vpp_id=$vpp_id"

    order_json=$(curl -fsS -X POST "$base/orders" \
      -H "Authorization: Bearer $session_token" \
      -H 'Content-Type: application/json' \
      -d "{\"vpp_id\":\"$vpp_id\",\"side\":\"buy\",\"price\":80,\"qty_kwh\":0.05,\"product_id\":\"$product_id\",\"purpose\":\"battery\"}")
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

  contracts)
    mkdir -p docs
    "$PY" -m eflux.cli openapi > docs/openapi.json
    "$PY" -m eflux.cli agent-spec-schema > docs/agent_spec.schema.json
    python3 scripts/generate_openapi_ts.py
    echo "regenerated checked-in contracts"
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
    exec "$PY" -m alembic upgrade head
    ;;

  makemigration)
    msg="${2:-}"
    if [[ -z "$msg" ]]; then
      echo "usage: ./tasks.sh makemigration \"<message>\"" >&2
      exit 1
    fi
    exec "$PY" -m alembic revision --autogenerate -m "$msg"
    ;;

  test)
    shift  # drop "test", forward the rest to pytest
    exec "$PY" -m pytest tests "$@"
    ;;

  verify)
    tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/eflux-verify.XXXXXX")
    trap 'rm -rf "$tmp_dir"' EXIT
    cp docs/openapi.json "$tmp_dir/openapi.json"
    cp docs/agent_spec.schema.json "$tmp_dir/agent_spec.schema.json"
    cp frontend/src/api/schema.gen.ts "$tmp_dir/schema.gen.ts"
    "$PY" -m ruff check src tests examples
    "$PY" -m mypy src/eflux/agents/decision.py src/eflux/market \
      src/eflux/ecosystem/release_contract.py src/eflux/ecosystem/deployment.py
    "$PY" -m pytest tests -q
    "$PY" scripts/verify_checkpoints.py
    EFLUX_DB_URL="sqlite+aiosqlite:///$tmp_dir/fresh.db" "$PY" -m alembic upgrade head
    EFLUX_DB_URL="sqlite+aiosqlite:///$tmp_dir/fresh.db" "$PY" -m alembic check
    EFLUX_DB_URL="sqlite+aiosqlite:///$tmp_dir/upgrade.db" "$PY" -m alembic upgrade 0009
    EFLUX_DB_URL="sqlite+aiosqlite:///$tmp_dir/upgrade.db" "$PY" -m alembic upgrade head
    pnpm -C frontend build
    ./tasks.sh contracts
    cmp "$tmp_dir/openapi.json" docs/openapi.json
    cmp "$tmp_dir/agent_spec.schema.json" docs/agent_spec.schema.json
    cmp "$tmp_dir/schema.gen.ts" frontend/src/api/schema.gen.ts
    ;;

  train-ppo)
    shift  # drop "train-ppo", forward the rest to the trainer
    exec "$PY" -m eflux.agents.ppo.train "$@"
    ;;

  bench)
    shift  # drop "bench", forward the rest to the benchmark runner
    exec "$PY" -m eflux.agents.bench.run "$@"
    ;;

  eval-ppo)
    shift  # drop "eval-ppo", forward the rest to the evaluator
    exec "$PY" -m eflux.agents.ppo.eval "$@"
    ;;

  backtest)
    shift  # drop "backtest", forward the rest to the backtest CLI
    exec "$PY" -m eflux.cli backtest "$@"
    ;;

  eval-worker)
    shift  # drop "eval-worker", forward the rest to the worker
    exec "$PY" -m eflux.evaluation.worker "$@"
    ;;

  ecosystem-worker)
    shift
    exec "$PY" -m eflux.ecosystem.worker "$@"
    ;;

  *)
    echo "Unknown task: $task. Run './tasks.sh help' for options." >&2
    exit 1
    ;;
esac
