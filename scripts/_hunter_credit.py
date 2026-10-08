#!/usr/bin/env python3
"""Hunter account credit check without shelling the key."""
import json
import urllib.request
from pathlib import Path

key = None
for line in Path("/home/boxed/.hermes/.env").read_text().splitlines():
    if line.startswith("HUNTER_API_KEY="):
        key = line.split("=", 1)[1].strip().strip('"').strip("'")
if not key:
    raise SystemExit("no HUNTER_API_KEY")
with urllib.request.urlopen(
    f"https://api.hunter.io/v2/account?api_key={key}", timeout=25
) as r:
    d = json.load(r)["data"]
print("plan:", d.get("plan_name"))
print("requests:", json.dumps(d.get("requests"), indent=1))
print("reset:", d.get("reset_date"))
