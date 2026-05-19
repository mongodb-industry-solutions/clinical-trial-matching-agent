#!/usr/bin/env bash
# Stop everything scripts/start-demo.sh started.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$REPO_ROOT/logs"
PIDS="$LOGS/pids"

stop_one() {
  local name="$1"
  local expected_fragment="$2"
  local pidfile="$PIDS/$name.pid"
  local portfile="$PIDS/$name.port"
  local pid=""
  local command=""

  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [ -n "$command" ] && [[ "$command" == *"$expected_fragment"* ]]; then
        kill "$pid" 2>/dev/null || true
        for ((i=0; i<5; i++)); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
          kill -9 "$pid" 2>/dev/null || true
        fi
        echo "Stopped $name (pid $pid)"
      else
        echo "Skipping $name pid $pid because it no longer looks like the demo process."
      fi
    fi
    rm -f "$pidfile"
  fi

  rm -f "$portfile"
}

stop_one frontend "next dev"
stop_one backend "uvicorn main:app"

echo "All stopped."
