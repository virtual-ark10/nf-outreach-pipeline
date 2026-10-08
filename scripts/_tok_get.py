#!/usr/bin/env python3
"""Fetch a CRM API path using PAD_TOKEN from the pad .env. Prints JSON."""
import json
import sys
import urllib.request
from pathlib import Path

env = Path("/home/boxed/resend-pad/.env").read_text().splitlines()
tok = None
for line in env:
    if line.startswith("PAD_TOKEN="):
        tok = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
if not tok:
    sys.exit("no PAD_TOKEN")

path = sys.argv[1] if len(sys.argv) > 1 else "/api/meta"
base = "http://127.0.0.1:3002"
req = urllib.request.Request(base + path, headers={"X-CRM-Token": tok})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode()
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print("HTTP", e.code)
print(body)
