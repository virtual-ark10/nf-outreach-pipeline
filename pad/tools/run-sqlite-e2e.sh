#!/bin/bash
# ============================================================================
#  tools/run-sqlite-e2e.sh — one command to prove the SQLite rewrite works.
#
#  Resets a throwaway database, migrates the old JSON into it, starts both real
#  services on test ports, runs tools/test-sqlite-e2e.cjs against them, then tears
#  everything down. Never touches the live ports (3001/3002) or the live database.
#
#  Usage:  bash tools/run-sqlite-e2e.sh
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

DB=${TEST_DB:-/tmp/e2e.db}
PAD_PORT=${TEST_PAD_PORT:-3997}
CRM_PORT=${TEST_CRM_PORT:-3998}
TOKEN=${TEST_TOKEN:-testtoken}
PAD_LOG=/tmp/e2e-pad.log
CRM_LOG=/tmp/e2e-crm.log
MIG_LOG=/tmp/e2e-migrate.log

# Never kill by process name (other agents' node processes share this box):
# find whoever owns the port.
kill_port() {
  local port="$1"
  local pids
  pids=$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $NF}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  [ -z "$pids" ] && return 0
  for pid in $pids; do kill "$pid" 2>/dev/null; done
  sleep 1
  for pid in $pids; do kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null; done
}

cleanup() {
  [ -n "${CRM_PID:-}" ] && kill "$CRM_PID" 2>/dev/null
  [ -n "${PAD_PID:-}" ] && kill "$PAD_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

echo "== resetting $DB =="
kill_port "$PAD_PORT"
kill_port "$CRM_PORT"
rm -f "${DB}" "${DB}-wal" "${DB}-shm"
PAD_DOMAINS="${PAD_DOMAINS:-newsletterfit.com}" node tools/migrate-json.cjs --db "$DB" > "$MIG_LOG" 2>&1 \
  || { echo "migration failed:"; tail -20 "$MIG_LOG"; exit 1; }
grep -E '^(inserted|table counts):' "$MIG_LOG" || true

echo "== starting services on $PAD_PORT / $CRM_PORT =="
OUTREACH_DB="$DB" CRM_PORT="$CRM_PORT" CRM_HOST=127.0.0.1 CRM_TOKEN="$TOKEN" PAD_TOKEN="$TOKEN" \
  PAD_URL="http://127.0.0.1:$PAD_PORT" BRAND_NAME=NewsletterFIT \
  node leads/server.cjs > "$CRM_LOG" 2>&1 &
CRM_PID=$!

# The pad needs the real webhook secret to verify signed webhooks in the test.
set -a
# shellcheck disable=SC1091
. ./.env
set +a
OUTREACH_DB="$DB" PORT="$PAD_PORT" PAD_TOKEN="$TOKEN" PAD_DOMAINS=newsletterfit.com \
  BRAND_NAME=NewsletterFIT node server.cjs > "$PAD_LOG" 2>&1 &
PAD_PID=$!

for _ in $(seq 1 30); do
  ok_crm=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CRM_PORT/api/health" || true)
  ok_pad=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PAD_PORT/api/health" || true)
  [ "$ok_crm" = "200" ] && [ "$ok_pad" = "200" ] && break
  sleep 0.5
done
if [ "$ok_crm" != "200" ] || [ "$ok_pad" != "200" ]; then
  echo "services did not come up (crm=$ok_crm pad=$ok_pad)"
  echo "--- crm log ---"; tail -20 "$CRM_LOG"
  echo "--- pad log ---"; tail -20 "$PAD_LOG"
  exit 1
fi

echo "== running the suite =="
OUTREACH_DB="$DB" TEST_PAD="http://127.0.0.1:$PAD_PORT" TEST_CRM="http://127.0.0.1:$CRM_PORT" TEST_TOKEN="$TOKEN" \
  node tools/test-sqlite-e2e.cjs
STATUS=$?

echo "== service logs (tail) =="
echo "--- crm ---"; tail -5 "$CRM_LOG"
echo "--- pad ---"; tail -5 "$PAD_LOG"
exit $STATUS
