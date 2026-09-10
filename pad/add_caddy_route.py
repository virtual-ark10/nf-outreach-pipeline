#!/usr/bin/env python3
"""Add /pad/* route to Caddy via its local admin API (no sudo needed).

routes added (before the catch-all):
  /pad   -> 301 redirect to /pad/
  /pad/* -> rewrite (strip /pad) + reverse_proxy 127.0.0.1:3001

Idempotent: GET /config/ -> insert -> POST /load (full replace).
"""
import json
import urllib.request

ADMIN = "http://127.0.0.1:2019"

def request(method, path, body=None, ctype=None):
    headers = {"Content-Type": ctype} if ctype else {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ADMIN + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        print(f"HTTP {e.code}: {raw[:500]}")
        raise

status, cfg = request("GET", "/config/")
print(f"GET /config/ -> {status}")

routes = cfg["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"]
for i, r in enumerate(routes):
    paths = (r.get("match") or [{}])[0].get("path", [])
    print(f"  existing[{i}] match={paths}")

# idempotency
if any((r.get("match") or [{}])[0].get("path") == ["/pad/*"] for r in routes):
    print("pad route already present; nothing to do")
    raise SystemExit(0)

redirect_route = {
    "handle": [{
        "handler": "static_response",
        "status_code": 301,
        "headers": {"Location": ["/pad/"]},
    }],
    "match": [{"path": ["/pad"]}],
}
proxy_route = {
    "handle": [
        {"handler": "rewrite", "strip_path_prefix": "/pad"},
        {"handler": "reverse_proxy", "upstreams": [{"dial": "127.0.0.1:3001"}]},
    ],
    "match": [{"path": ["/pad/*"]}],
}

catchall_idx = None
for i, r in enumerate(routes):
    paths = (r.get("match") or [{}])[0].get("path", [])
    if not paths:
        catchall_idx = i
        break
insert_at = catchall_idx if catchall_idx is not None else len(routes)
routes.insert(insert_at, proxy_route)
routes.insert(insert_at, redirect_route)
print(f"inserted pad routes at index {insert_at}")

status, _ = request("POST", "/load", cfg, ctype="application/json")
print(f"POST /load -> {status}")

status, routes = request("GET", "/config/apps/http/servers/srv0/routes/0/handle/0/routes")
for i, r in enumerate(routes):
    paths = (r.get("match") or [{}])[0].get("path", [])
    handlers = [h.get("handler") for h in r.get("handle", [])]
    print(f"  final[{i}] match={paths} handlers={handlers}")
print("DONE")