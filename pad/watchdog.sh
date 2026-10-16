#!/bin/bash
# Resend Pad watchdog — restart if down, re-apply Caddy route if missing.
# Runs every minute via crontab. Also re-applies the route on reboot.
LOG=/home/boxed/resend-pad/watchdog.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 1. Server up?
if ! curl -sf http://127.0.0.1:3001/api/health > /dev/null 2>&1; then
  echo "$(ts) server down — restarting" >> "$LOG"
  # Prefer the PID that actually holds :3001 — precise, and it cannot touch the
  # leads engine ("node server.cjs --leads-engine") or any other node process.
  PID=$(ss -tlnp 2>/dev/null | grep ':3001' | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -n "$PID" ]; then kill "$PID" 2>/dev/null; else pkill -f 'node server\.cjs$' 2>/dev/null; fi
  sleep 1
  nohup /home/boxed/resend-pad/boot.sh >> "$LOG" 2>&1 &
  sleep 2
fi

# 2. Caddy route present? (idempotent re-apply via admin API)
#
# The route lives in Caddy's RUNTIME config, not in the Caddyfile: a Caddyfile is
# static, and any `systemctl reload caddy` (the deploy, the caddy-headers cron)
# rebuilds the config from the file and drops the pad route, so /pad/ starts
# answering the Astro site's 404 until this watchdog puts it back.
#
# The check MUST be host-scoped. The old version searched every server for a route
# whose path was /pad/*, and starterlens.com has exactly that route, so it reported
# "present" while newsletterfit.com/pad was gone — the pad 404'd for as long as
# nothing else fixed it (2026-10-16). Look only inside the newsletterfit.com block.
route_state() {
  curl -sf http://127.0.0.1:2019/config/ | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
def inner_routes(cfg, host):
    for srv in cfg.get("apps", {}).get("http", {}).get("servers", {}).values():
        for route in srv.get("routes", []):
            hosts = [h for m in (route.get("match") or []) for h in (m.get("host") or [])]
            if host in hosts:
                for h in (route.get("handle") or []):
                    if h.get("handler") == "subroute":
                        return h.get("routes") or []
    return []
paths = [p for r in inner_routes(cfg, "newsletterfit.com")
         for m in (r.get("match") or []) for p in (m.get("path") or [])]
print("yes" if "/pad/*" in paths else "no")
' 2>/dev/null
}
ROUTE_OK=$(route_state)
if [ "$ROUTE_OK" != "yes" ]; then
  echo "$(ts) Caddy route missing for newsletterfit.com — re-applying" >> "$LOG"
  /home/boxed/resend-pad/add_caddy_route.py >> "$LOG" 2>&1 || echo "$(ts) route re-apply FAILED" >> "$LOG"
  # Verify the re-apply instead of trusting it: say so either way, because a silent
  # failure here is a pad that 404s for the public.
  if [ "$(route_state)" = "yes" ]; then
    echo "$(ts) pad route restored" >> "$LOG"
  else
    echo "$(ts) PAD ROUTE STILL MISSING — /pad/ is 404 for the public" >> "$LOG"
  fi
fi