#!/bin/bash
# Verify the outreach click-tracking chain after a token merge + API restart.
# Usage: verify-tracking-links.sh <lt-token> [more tokens...]
# Expects, per token: HTTP 302 + Location header + `set-cookie: nf_attr=...`.
# Also probes the pageview beacon (/api/attribution/pv) → expect 204.
set -u
if [ $# -eq 0 ]; then
  echo "usage: $0 <lt-token> [more...]"
  echo "hint: pass tokens from the store or attribution.new-tokens.json"
  exit 2
fi
HDR=$(mktemp)
rc=0
for t in "$@"; do
  code=$(curl -sI -o "$HDR" -w "%{http_code}" "https://newsletterfit.com/api/click?lt=$t")
  printf "token %s -> /api/click %s\n" "${t:0:12}" "$code"
  grep -i "^location:" "$HDR" | sed 's/^/  location: /'
  grep -i "^set-cookie:" "$HDR" | sed 's/nf_attr=[^;]*/nf_attr=<redacted>/; s/^/  cookie: /'
  [ "$code" = "302" ] || rc=1
done
pv=$(curl -s -o /dev/null -w "%{http_code}" "https://newsletterfit.com/api/attribution/pv?path=/pricing")
printf "beacon /api/attribution/pv -> %s (expect 204)\n" "$pv"
[ "$pv" = "204" ] || rc=1
rm -f "$HDR"
[ "$rc" -eq 0 ] && echo "OK: click chain + beacon live" || echo "FAILED: see above"
exit $rc