#!/usr/bin/env bash
set -euo pipefail

# Starts a temporary local SQLite-backed TPT server and runs the HTTP e2e suite.
# Use scripts/e2e_http.py directly when targeting an already deployed service.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d /tmp/tpt-e2e-local-XXXXXX)"
PORT="${TPT_E2E_PORT:-8765}"
API_KEY="${TPT_E2E_API_KEY:-tpt-e2e-api-key}"
WRITE_KEY="${TPT_E2E_WRITE_KEY:-tpt-e2e-write-key}"
PID=""

cleanup() {
  if [[ -n "$PID" ]]; then
    kill "$PID" >/dev/null 2>&1 || true
    wait "$PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cd "$ROOT_DIR"
DATABASE_MODE=sqlite \
SQLITE_PATH="$TMP_DIR/persona_tracking.db" \
API_KEY="$API_KEY" \
WRITE_KEY="$WRITE_KEY" \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$TMP_DIR/server.log" 2>&1 &
PID="$!"

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    cat "$TMP_DIR/server.log" >&2 || true
    echo "TPT local server exited before becoming healthy" >&2
    exit 1
  fi
  sleep 0.5
done

TPT_E2E_BASE_URL="http://127.0.0.1:$PORT" \
TPT_E2E_API_KEY="$API_KEY" \
TPT_E2E_WRITE_KEY="$WRITE_KEY" \
TPT_E2E_ENV=staging \
python3 scripts/e2e_http.py
