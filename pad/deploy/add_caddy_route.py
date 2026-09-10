#!/usr/bin/env python3
"""Expose the Leads CRM at /crm/* through Caddy's running config (no root).

Uses the pad-style shape: rewrite strip_path_prefix '/crm' then reverse_proxy,
so the backend sees clean /api/... paths. Idempotent.
"""
import json
import urllib.request
import urllib.error

ADMIN = "http://127.0.0.1:2019"
UPSTREAM = "127.0.0.1:3002"
MATCH_PATH = "/crm/*"
BARE_PATH = "/crm"


def request(method, path, body=None, ctype=None):
    headers = {"Content-Type": ctype} if ctype else {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ADMIN + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:500]}")
        raise


_, cfg = request("GET", "/config/")
server = None
for name, srv in cfg["apps"]["http"]["servers"].items():
    if any(isinstance(r, dict) for r in srv.get("routes", [])):
        server = name
        break
if server is None:
    raise SystemExit("no caddy server found")
routes = cfg["apps"]["http"]["servers"][server]["routes"]
# the app routes live inside the first route's subroute handle
inner = None
for r in routes:
    for h in r.get("handle", []):
        if h.get("handler") == "subroute" and h.get("routes"):
            inner = h["routes"]
            break
    if inner:
        break
if inner is None:
    raise SystemExit("could not locate the subroute holding the app routes")

paths = [(r.get("match") or [{}])[0].get("path", []) for r in inner]
if any(MATCH_PATH in (p or []) for p in paths):
    print(f"route {MATCH_PATH} already present; nothing to do")
    raise SystemExit(0)

redirect = {
    "match": [{"path": [BARE_PATH]}],
    "handle": [{"handler": "static_response", "status_code": 301,
                "headers": {"Location": ["/crm/"]}}],
    "terminal": True,
}
strip_and_proxy = {
    "match": [{"path": [MATCH_PATH]}],
    "handle": [
        {"handler": "rewrite", "strip_path_prefix": "/crm"},
        {"handler": "reverse_proxy", "upstreams": [{"dial": UPSTREAM}]},
    ],
    "terminal": True,
}

# insert BEFORE the catch-all (first route with no path match)
insert_at = next((i for i, p in enumerate(paths) if not p), len(inner))
inner.insert(insert_at, strip_and_proxy)
inner.insert(insert_at, redirect)
print(f"inserted {BARE_PATH} redirect + {MATCH_PATH} -> {UPSTREAM} at index {insert_at}")

status, _ = request("POST", "/load", cfg, ctype="application/json")
print(f"POST /load -> {status}")

_, after = request("GET", "/config/")
found = "/crm/*" in json.dumps(after)
print("route now present:", found)
print("DONE")
