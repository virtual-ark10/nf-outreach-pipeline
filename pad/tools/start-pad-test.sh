#!/bin/bash
# Test harness: start the pad on port 3997 against the throwaway e2e database,
# with the real webhook secret (so signed webhooks verify) but a test pad token.
cd /home/boxed/resend-pad || exit 1
set -a
# shellcheck disable=SC1091
. ./.env
set +a
export OUTREACH_DB=/tmp/e2e.db
export PORT=3997
export PAD_TOKEN=testtoken
export PAD_DOMAINS=newsletterfit.com
export BRAND_NAME=NewsletterFIT
exec node server.cjs
