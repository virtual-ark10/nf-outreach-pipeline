#!/bin/bash
# Resend Pad boot script — loads .env and starts the server on 127.0.0.1:3001
cd /home/boxed/resend-pad || exit 1

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