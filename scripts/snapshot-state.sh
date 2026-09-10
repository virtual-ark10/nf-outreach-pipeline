#!/bin/bash
# Refresh this repo's snapshot of the live runtime state.
#
# The CRM (leads + stages + activity) and the mail log live on the box. Resend
# only retains email for 30 days, so this repo is the off-box archive — run this
# after any meaningful batch and commit the result.
#
# Usage: ./scripts/snapshot-state.sh [--commit]

set -euo pipefail

LIVE_PAD="${LIVE_PAD:-/home/boxed/resend-pad}"
LIVE_ATTR="${LIVE_ATTR:-/home/boxed/newsletterfit/attribution}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "repo:  $REPO"
echo "live:  $LIVE_PAD"

# --- mail + CRM state ---
rsync -a --exclude='*.bak-*' --exclude='*.pre-import' --exclude='*.pre-token-fix' \
      --exclude='*.log' "$LIVE_PAD/data/" "$REPO/pad/data/"
rsync -a --exclude='*.bak-*' --exclude='*.pre-import' --exclude='*.log' \
      "$LIVE_PAD/leads/data/" "$REPO/pad/leads/data/"

# --- attribution token store ---
rsync -a --exclude='*.bak-*' "$LIVE_ATTR/" "$REPO/attribution/" \
      --include='*.json' --include='*.csv' --exclude='*'

# --- code (the deployment is the source of truth for the running version) ---
rsync -a --exclude='.env' --exclude='.env.bak*' --exclude='*.log' \
      --exclude='data/' --exclude='leads/data/' --exclude='leads/attic/' \
      --exclude='node_modules/' --exclude='__pycache__/' \
      --exclude='*.bak-*' --exclude='*.pre-import' --exclude='*.pre-token-fix' \
      "$LIVE_PAD/" "$REPO/pad/"

cd "$REPO"
git add -A
git --no-pager diff --cached --stat

if [ "${1:-}" = "--commit" ]; then
  STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  LEADS="$(python3 -c "import json;print(len(json.load(open('pad/leads/data/crm.json'))['leads']))" 2>/dev/null || echo '?')"
  git commit -m "chore: refresh runtime snapshot ($STAMP, $LEADS leads)"
  echo "committed. push with: git push"
else
  echo "(dry run — re-run with --commit to commit)"
fi
