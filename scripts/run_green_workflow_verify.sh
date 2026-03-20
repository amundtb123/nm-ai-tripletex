#!/usr/bin/env bash
# Local verification against NM sandbox — requires valid token in local.solve_list_employees.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source .venv/bin/activate
PORT="${VERIFY_PORT:-9876}"
LOG="${VERIFY_LOG:-/tmp/aa-verify-green.log}"
rm -f "$LOG"
export PORT
(lsof -ti:"$PORT" | xargs kill -9 2>/dev/null) || true
sleep 1
python main.py >>"$LOG" 2>&1 &
PID=$!
sleep 2
kill_server() { kill "$PID" 2>/dev/null || true; }
trap kill_server EXIT

CREDS="$(python3 -c "import json; d=json.load(open('examples/local.solve_list_employees.json')); print(json.dumps(d['tripletex_credentials']))")"

post() {
  local name="$1"
  local prompt="$2"
  echo "=== $name ===" >>"$LOG"
  curl -sS -X POST "http://127.0.0.1:${PORT}/solve" \
    -H "Content-Type: application/json" \
    -d "{\"prompt\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$prompt"),\"files\":[],\"tripletex_credentials\":$CREDS}" \
    >>"$LOG" 2>&1 || true
  echo "" >>"$LOG"
}

post "list_employees" "list employees"
post "search_customer" "finn kunde Acme"
post "search_product" "finn produkt Kaffe"
post "create_customer" "opprett kunde Agent Verify NM 20260320"
post "create_product" "opprett produkt Agent Verify Vare 20260320 varenummer: AV-7320 pris 99 kr"

echo "Log written to $LOG"
