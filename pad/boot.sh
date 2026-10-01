#!/bin/bash
# Resend Pad boot script — loads .env and starts the server on 127.0.0.1:3001
cd /home/boxed/resend-pad || exit 1

# Pin a Node runtime that has node:sqlite (db.cjs needs it; Node >= 22).
# Cron runs with a minimal PATH and used to pick /usr/bin/node (v20), which
# crashed the pad AND the leads engine on startup with ERR_UNKNOWN_BUILTIN_MODULE.
if [ -x /home/boxed/.local/bin/node ]; then
  export PATH="/home/boxed/.local/bin:$PATH"
fi
if ! node -e "require('node:sqlite')" >/dev/null 2>&1; then
  echo "$(date '+%F %T') boot.sh: no node with node:sqlite on PATH; refusing to start" >> /home/boxed/resend-pad/pad.log
  exit 1
fi

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

# Defaults if not in .env
: "${PORT:=3001}"
: "${DATA_DIR:=/home/boxed/resend-pad/data}"

# Outreach link minting credentials — shared corpus.env is the source of truth.
# The pad needs NEWSLETTERFIT_API + API_BEARER_TOKEN to re-mint dead old-format
# tracking links at send time (fail-closed: sends with such links are blocked).
if [ -f /home/boxed/.config/newsletterfit/corpus.env ]; then
  API_LINE="$(grep -E '^NEWSLETTERFIT_API=' /home/boxed/.config/newsletterfit/corpus.env | head -1)"
  TOKEN_LINE="$(grep -E '^API_BEARER_TOKEN=' /home/boxed/.config/newsletterfit/corpus.env | head -1)"
  [ -n "$API_LINE" ] && export NEWSLETTERFIT_API="${API_LINE#*=}"
  [ -n "$TOKEN_LINE" ] && export API_BEARER_TOKEN="${TOKEN_LINE#*=}"
fi

export PORT DATA_DIR

exec node server.cjs >> /home/boxed/resend-pad/pad.log 2>&1