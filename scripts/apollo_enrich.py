#!/usr/bin/env python3
"""Apollo.io REST enrichment helper — free-plan-aware.
Free plan: POST /api/v1/people/match works (enrich by email or name+org; ~1 credit each success).
Search endpoints (mixed_people/*) are PAID-plan only (403 API_INACCESSIBLE) — don't waste calls.
Auth: x-api-key header. Base: https://app.apollo.io/api/v1/
"""
import json, sys, urllib.request

API_KEY = "mF2p_Rv0nobI7X-5iXInFQ"
BASE = "https://app.apollo.io/api/v1"

def call(ep, payload):
    req = urllib.request.Request(BASE + ep, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-Api-Key": API_KEY}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def health():
    req = urllib.request.Request(BASE + "/users/api_profile", headers={"X-Api-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "health"
    if mode == "health":
        print(json.dumps(health(), indent=2)); raise SystemExit(0)
    payload = json.load(sys.stdin)
    if mode == "enrich":
        # input: {"email": "..."} OR {"first_name","last_name","organization_name"}/{"domain"}
        result = call("/people/match", payload)
        p = result.get("person") or {}
        print(json.dumps(result, indent=2))
        print("\n---SHORT---")
        print(f"name={p.get('name')} title={p.get('title')} org={(p.get('organization') or {}).get('name')} email={p.get('email')} linkedin={p.get('linkedin_url')}")
    elif mode == "company":
        # input: {"domain": "acme.com"}
        result = call("/organizations/enrich", payload)
        o = result.get("organization") or {}
        print(json.dumps(result, indent=2))
        print("\n---SHORT---")
        print(f"name={o.get('name')} domain={o.get('domain')} size={o.get('estimated_num_employees')} industry={o.get('primary_industry', {}).get('name') if isinstance(o.get('primary_industry'), dict) and o.get('primary_industry') else o.get('primary_industry')}")
    else:
        raise SystemExit(f"unknown mode {mode}")
