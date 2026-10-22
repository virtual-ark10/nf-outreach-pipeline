#!/usr/bin/env bash
# One-off catch-up pass over the uncontacted backlog, run by hand on 2026-10-19.
# Names first and free, then the address engines. The paid finder stops where the month's
# $0.30 cap already is ($0.288 spent), so this pass is almost entirely the free scrape and
# the free name lanes; that is the point, it costs nothing to cover the whole pool.
set -uo pipefail
REPO=/home/boxed/nf-outreach-pipeline
export PATH="/home/boxed/.local/bin:$HOME/.cargo/bin:$PATH"
cd "$REPO" || exit 1
LOG="$REPO/data/backlog-discovery-$(date +%Y%m%d_%H%M).log"
{
  echo "=== backlog discovery start $(date -Is)"
  echo "--- names (free lanes)"
  python3 scripts/fill_contact_names.py --apply 2>&1
  echo "--- addresses (free scrape, then paid find inside the month cap)"
  python3 scripts/discover_contacts.py --apply --max 208 --max-finds 3 --budget 0.012 --monthly-cap 0.30 2>&1
  echo "=== backlog discovery done $(date -Is) rc=$?"
} > "$LOG" 2>&1
echo "log: $LOG"
