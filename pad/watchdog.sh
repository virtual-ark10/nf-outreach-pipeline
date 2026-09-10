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
ROUTE_OK=$(curl -sf http://127.0.0.1:2019/config/ | python3 -c '
import json,sys
cfg = json.load(sys.stdin)
found = False
for r in cfg.get("apps", {}).get("http", {}).get("servers", {}).values():
    for route in r.get("routes", []):
        for m in route.get("match", []):
            if "/pad/*" in m.get("path", []):
                found = True
print("yes" if found else "no")
' 2>/dev/null)
if [ "$ROUTE_OK" != "yes" ]; then
  echo "$(ts) Caddy route missing — re-applying" >> "$LOG"
  /home/boxed/resend-pad/add_caddy_route.py >> "$LOG" 2>&1 || echo "$(ts) route re-apply FAILED" >> "$LOG"
fi