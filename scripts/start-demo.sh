#!/usr/bin/env bash
# Start the Clinical Trial Matching demo locally.
# Backgrounds the FastAPI backend with logs in ./logs, then runs Next.js
# in the foreground. Ctrl-C in the frontend pane cleans up the backend too.
# Manual stop: scripts/stop-demo.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"
FRONTEND_ROOT="$REPO_ROOT/frontend"
LOGS="$REPO_ROOT/logs"
PIDS="$LOGS/pids"
BACKEND_PORT="${BACKEND_PORT:-8014}"
FRONTEND_PORT="${PORT:-3014}"
RESERVED_PORTS=()
CLAIMED_PORT=""
BACKEND_CMD=""
BACKEND_RUNTIME_LABEL=""

port_is_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

port_is_reserved() {
  local port="$1"
  local reserved_port=""

  if [ "${#RESERVED_PORTS[@]}" -eq 0 ]; then
    return 1
  fi

  for reserved_port in "${RESERVED_PORTS[@]}"; do
    if [ "$reserved_port" = "$port" ]; then
      return 0
    fi
  done

  return 1
}

find_available_port() {
  local preferred_port="$1"
  local candidate="$preferred_port"
  local offset=0

  while [ "$offset" -lt 50 ]; do
    if ! port_is_listening "$candidate" && ! port_is_reserved "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
    candidate=$((preferred_port + offset + 1))
    offset=$((offset + 1))
  done

  return 1
}

claim_port() {
  local requested_port="$1"
  local service_name="$2"
  local selected_port=""

  selected_port="$(find_available_port "$requested_port")" || {
    echo "ERROR: could not find an available port for $service_name starting at $requested_port." >&2
    exit 1
  }

  if [ "$selected_port" != "$requested_port" ]; then
    echo "NOTE: port $requested_port is already in use. Starting $service_name on $selected_port instead." >&2
  fi

  RESERVED_PORTS+=("$selected_port")
  CLAIMED_PORT="$selected_port"
}

resolve_backend_command() {
  if [ -x "$BACKEND_ROOT/.venv/bin/python" ] && [ -x "$BACKEND_ROOT/.venv/bin/uvicorn" ]; then
    BACKEND_RUNTIME_LABEL="$BACKEND_ROOT/.venv"
    BACKEND_CMD="\"$BACKEND_ROOT/.venv/bin/python\" -m uvicorn main:app --host 127.0.0.1 --port ${BACKEND_PORT}"
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    BACKEND_RUNTIME_LABEL="uv run"
    BACKEND_CMD="uv run uvicorn main:app --host 127.0.0.1 --port ${BACKEND_PORT}"
    return 0
  fi

  return 1
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local tries="${3:-45}"
  local pidfile="$PIDS/$name.pid"
  local pid=""

  for ((i=0; i<tries; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi

    if [ -f "$pidfile" ]; then
      pid="$(cat "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
    fi

    sleep 1
  done

  echo "ERROR: $name did not become ready at $url after ${tries}s." >&2
  echo "       Tail $LOGS/$name.log for details." >&2
  return 1
}

start_service() {
  local name="$1"
  local workdir="$2"
  local cmd="$3"

  (
    cd "$workdir"
    eval "exec $cmd"
  ) >"$LOGS/$name.log" 2>&1 &

  echo $! >"$PIDS/$name.pid"
  disown %+ 2>/dev/null || true
  echo "  started $name (pid $!)"
}

cleanup() {
  local exit_code=$?
  local backend_pid=""
  trap - EXIT

  if [ -f "$PIDS/backend.pid" ]; then
    backend_pid="$(cat "$PIDS/backend.pid" 2>/dev/null || true)"
  fi

  echo
  echo "Stopping demo services..."
  "$REPO_ROOT/scripts/stop-demo.sh" || true
  if [ -n "$backend_pid" ]; then
    wait "$backend_pid" 2>/dev/null || true
  fi
  exit "$exit_code"
}

echo "Checking demo prerequisites..."
if [ ! -f "$BACKEND_ROOT/.env" ]; then
  echo "ERROR: missing config file: backend/.env" >&2
  echo "       Start from backend/.env.example, then re-run this script." >&2
  exit 1
fi

if [ ! -f "$FRONTEND_ROOT/package.json" ]; then
  echo "ERROR: missing frontend/package.json; repo layout is not what this script expects." >&2
  exit 1
fi

if [ ! -d "$FRONTEND_ROOT/node_modules" ]; then
  echo "ERROR: frontend dependencies are not installed." >&2
  echo "       Run 'make frontend-install' once, then re-run this script." >&2
  exit 1
fi

if [ ! -x "$FRONTEND_ROOT/node_modules/.bin/next" ]; then
  echo "ERROR: frontend Next.js runtime is missing." >&2
  echo "       Run 'make frontend-install' once, then re-run this script." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node is not installed or not on PATH." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required for startup health checks." >&2
  exit 1
fi

if [ ! -f "$FRONTEND_ROOT/.env.local" ]; then
  echo "NOTE: frontend/.env.local is missing; this run will inject the backend URL via shell env." >&2
fi

claim_port "$BACKEND_PORT" "backend"
BACKEND_PORT="$CLAIMED_PORT"
claim_port "$FRONTEND_PORT" "frontend"
FRONTEND_PORT="$CLAIMED_PORT"

if ! resolve_backend_command; then
  echo "ERROR: could not find a usable backend runtime." >&2
  echo "       Checked backend/.venv/bin and the 'uv' command on PATH." >&2
  echo "       Run 'make backend-install' once, then re-run this script." >&2
  exit 1
fi

mkdir -p "$LOGS" "$PIDS"
rm -f "$PIDS/backend.pid" "$PIDS/frontend.pid" "$PIDS/backend.port" "$PIDS/frontend.port"
printf '%s\n' "$BACKEND_PORT" >"$PIDS/backend.port"
printf '%s\n' "$FRONTEND_PORT" >"$PIDS/frontend.port"

echo "Starting backend..."
echo "Using backend runtime: $BACKEND_RUNTIME_LABEL"
start_service "backend" "$BACKEND_ROOT" "$BACKEND_CMD"

echo "Waiting for backend health..."
wait_for_http "http://127.0.0.1:${BACKEND_PORT}/clinical-trials/health" "backend"

echo
echo "Backend is ready:"
echo "  API:  http://127.0.0.1:${BACKEND_PORT}"
echo "  Logs: $LOGS/backend.log"
echo
echo "Frontend env for this run:"
echo "  CLINICAL_TRIAL_MATCHING_AGENT_BACKEND_URL=http://127.0.0.1:${BACKEND_PORT}"
echo "  NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:${BACKEND_PORT}"
echo "  REACT_APP_BACKEND_URL=http://127.0.0.1:${BACKEND_PORT}"
echo
echo "Starting Next.js on http://localhost:${FRONTEND_PORT} (Ctrl-C to stop everything)..."
echo

trap cleanup EXIT

(
  cd "$FRONTEND_ROOT"
  export PORT="$FRONTEND_PORT"
  export CLINICAL_TRIAL_MATCHING_AGENT_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
  export NEXT_PUBLIC_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
  export REACT_APP_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
  bash -c '
    printf "%s\n" "$$" > "$1"
    exec ./node_modules/.bin/next dev --hostname 0.0.0.0 --port "$2"
  ' bash "$PIDS/frontend.pid" "$FRONTEND_PORT"
)
