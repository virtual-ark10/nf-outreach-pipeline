#!/bin/bash
# ============================================================================
#  tools/cutover-to-sqlite.sh — move the LIVE deployment onto SQLite.
#
#  What it does, in order:
#    1. backs up the JSON stores AND the pre-SQLite server code (rollback path)
#    2. migrates the JSON into <pad>/data/outreach.db
#    3. restarts the pad (:3001) and the leads engine (:3002) via boot.sh,
#       killing only the processes that actually hold those ports
#    4. verifies the live services answer correctly from the database
#
#  The JSON files are never deleted by this script.
#
#  Usage:  bash tools/cutover-to-sqlite.sh [--skip-restart]
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
STAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP="$ROOT/attic/pre-sqlite-$STAMP"
PAD_PORT=3001
CRM_PORT=3002

# The store location must be the default (<pad>/data/outreach.db). A stray
# OUTREACH_DB in the environment would silently point the live services at a
# throwaway file — and a /tmp database disappears on reboot.
unset OUTREACH_DB
echo "target database: $ROOT/data/outreach.db"

kill_port() {
  local port="$1" pids
  pids=$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $NF}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  [ -z "$pids" ] && return 0
  for pid in $pids; do kill "$pid" 2>/dev/null; done
  sleep 1
  for pid in $pids; do kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null; done
}

wait_health() {
  local url="$1" label="$2"
  for _ in $(seq 1 30); do
    curl -sf "$url/api/health" > /dev/null 2>&1 && { echo "  $label up"; return 0; }
    sleep 0.5
  done
  echo "  $label DID NOT COME UP"; return 1
}

echo "== 1. backup (JSON stores + pre-SQLite code) =="
mkdir -p "$BACKUP"
cp -a "$ROOT/data" "$BACKUP/data" 2>/dev/null
cp -a "$ROOT/leads/data" "$BACKUP/leads-data" 2>/dev/null
cp -a "$ROOT/server.cjs" "$BACKUP/server.cjs.old" 2>/dev/null
cp -a "$ROOT/leads/server.cjs" "$BACKUP/leads-server.cjs.old" 2>/dev/null
echo "  -> $BACKUP"
du -sh "$BACKUP" 2>/dev/null

echo "== 2. migrate into $ROOT/data/outreach.db =="
PAD_DOMAINS="${PAD_DOMAINS:-newsletterfit.com}" node tools/migrate-json.cjs --from "$ROOT" 2>&1 | sed 's/^/  /'

if [ "${1:-}" = "--skip-restart" ]; then
  echo "== --skip-restart: not touching the running services =="
  exit 0
fi

echo "== 3. restart the services (by port owner) =="
kill_port "$PAD_PORT"
kill_port "$CRM_PORT"
sleep 1
nohup "$ROOT/leads/boot.sh" >> "$ROOT/leads/watchdog.log" 2>&1 &
sleep 1
nohup "$ROOT/boot.sh" >> "$ROOT/watchdog.log" 2>&1 &
wait_health "http://127.0.0.1:$CRM_PORT" "leads engine :$CRM_PORT" || exit 1
wait_health "http://127.0.0.1:$PAD_PORT" "pad :$PAD_PORT" || exit 1

echo "== 4. verify against the live services =="
set -a
# shellcheck disable=SC1091
. "$ROOT/.env"
set +a
curl -s -H "X-CRM-Token: $PAD_TOKEN" "http://127.0.0.1:$CRM_PORT/api/meta" > /tmp/live-meta.json
curl -s -H "X-Pad-Token: $PAD_TOKEN" "http://127.0.0.1:$PAD_PORT/api/crm/leads" > /tmp/live-leads.json
curl -s -H "X-Pad-Token: $PAD_TOKEN" "http://127.0.0.1:$PAD_PORT/api/drafts" > /tmp/live-drafts.json
python3 - <<'PY'
import json
meta = json.load(open('/tmp/live-meta.json'))
leads = json.load(open('/tmp/live-leads.json'))
drafts = json.load(open('/tmp/live-drafts.json'))
print('  stage counts   :', {k: v for k, v in meta['counts'].items() if v})
print('  total leads    :', meta['total'], '| converted:', meta.get('converted'))
print('  storage        :', meta.get('storage'))
print('  leads via pad  :', len(leads.get('leads', [])), '| drafts via pad:', len(drafts.get('data', [])))
sample = next((l for l in leads.get('leads', []) if l['id'] == 'mcalvany'), None)
if sample:
    print('  mcalvany       : stage=%s sent=%s due=%s' % (sample['stage'], sample['emails_sent'], sample.get('next_due')))
ok = meta['total'] == 16 and len(leads.get('leads', [])) == 16 and len(drafts.get('data', [])) == 7
print('  VERDICT        :', 'OK — live data matches the migration' if ok else 'MISMATCH — check the numbers above')
PY
echo
echo "rollback if needed: restore $BACKUP/data, $BACKUP/leads-data and the .old server files, then run boot.sh"
