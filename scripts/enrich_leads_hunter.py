#!/usr/bin/env python3
"""Fill the missing contact address on CRM leads, from Hunter, for the ones a draft can use.

A lead with no address can never be drafted, and the pipeline's throughput is otherwise capped
by whatever the intake happened to carry. This closes that gap without inventing anything: it
asks Hunter for the domain's contacts, keeps the go-to-market, growth or marketing person (that
is who buys sponsorship intelligence), requires a real first name so the greeting is not
guessed, and writes the result back to the CRM.

Budget-aware: the free plan carries 50 requests, so it stops before spending the last few and
never guesses an address it could not verify.

Usage
  python3 scripts/enrich_leads_hunter.py                 # report only
  python3 scripts/enrich_leads_hunter.py --apply --max 8 # write the best find per lead
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

CRM_DB = "/home/boxed/nf-outreach-pipeline/pad/data/outreach.db"
CRM_API = "http://127.0.0.1:3002"
HUNTER = "https://api.hunter.io/v2"
ROLE_BY_DEPARTMENT = {
    "marketing": "marketin", "management": "gtm", "growth": "growth",
    "sales": "gtm", "communication": "marketin", "executive": "founder", "it": "other",
}
GENERIC_LOCALS = {"info", "hello", "contact", "support", "sales", "team", "admin", "press",
                  "enterprise", "business", "marketing", "media", "partners", "partnerships"}
# A buyer of sponsorship intelligence sits in marketing, content, growth or brand leadership.
# Recruiting-side brand roles look adjacent and are not: an employer brand manager buys no ads.
WEAK_ROLE = re.compile(r"(employer brand|recruit|talent|people ops|human resources|\bhr\b|"
                       r"employer marketing|university|campus)", re.I)
STRONG_ROLE = re.compile(r"(marketing|growth|brand|content|demand|media|partnership|sponsor|"
                         r"communications|founder|chief executive|\bcmo\b|\bceo\b)", re.I)

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--max", type=int, default=8, help="domains to look up this run")
ap.add_argument("--min-confidence", type=int, default=70)
args = ap.parse_args()


def load_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


ENV = {**load_env("/home/boxed/.env"), **load_env("/home/boxed/local-seo-leadgen/.env")}
KEY = ENV.get("HUNTER_API_KEY", "")
if not KEY:
    print("  no HUNTER_API_KEY in the usual env files: nothing to do")
    sys.exit(2)
PAD_TOKEN = load_env("/home/boxed/resend-pad/.env")["PAD_TOKEN"]


def hunter(path, **params):
    q = urllib.parse.urlencode({**params, "api_key": KEY})
    req = urllib.request.Request(f"{HUNTER}/{path}?{q}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)[:120]}


acct = hunter("account").get("data", {})
req = acct.get("requests", {}) or {}
remaining = int((req.get("credits") or {}).get("remaining") or 0)
print(f"  Hunter plan {acct.get('plan_name')}: {remaining} request(s) remaining")

con = sqlite3.connect(f"file:{CRM_DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
leads = [dict(r) for r in con.execute(
    "SELECT id, company, domain FROM leads WHERE stage='leads' AND COALESCE(email,'') = '' "
    "AND deleted_at IS NULL AND COALESCE(domain,'') <> '' ORDER BY company")]
con.close()
print(f"  {len(leads)} lead(s) with a domain and no address")

if remaining < len(leads[: args.max]) + 2:
    print(f"  budget guard: {remaining} left is not enough for {args.max} lookups plus headroom. "
          f"Lower --max or top the plan up.")
    sys.exit(0)


def score(contact):
    """Prefer the buyer, then a confident address, then a real first name."""
    dept = str(contact.get("department") or "").lower()
    sen = str(contact.get("seniority") or "").lower()
    conf = int(contact.get("confidence") or 0)
    local = str(contact.get("value") or "").split("@")[0].lower()
    given = str(contact.get("first_name") or "").strip()
    pts = 0
    pts += 30 if dept in ("marketing", "growth") else 20 if dept == "management" else 10 if dept == "sales" else 0
    pts += 20 if sen in ("executive", "senior") else 10 if sen == "junior" else 0
    pts += min(conf, 100) // 5
    pts += 10 if given and local not in GENERIC_LOCALS else -20
    return pts


def patch_lead(lead_id, payload):
    req2 = urllib.request.Request(f"{CRM_API}/api/leads/{lead_id}", method="PATCH",
                                 data=json.dumps(payload).encode(),
                                 headers={"X-CRM-Token": PAD_TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req2, timeout=45) as r:
            return r.status
    except Exception as e:
        return f"error {str(e)[:80]}"


picked = 0
for lead in leads:
    if picked >= args.max:
        print("  (run budget reached; the rest wait for the next run)")
        break
    domain = str(lead["domain"]).strip()
    d = hunter("domain-search", domain=domain, limit=10)
    picked += 1
    if "error" in d:
        print(f"  {lead['company'][:20]:<20} lookup failed: {d['error']}")
        continue
    data = d.get("data", {}) or {}
    emails = [e for e in (data.get("emails") or [])
              if int(e.get("confidence") or 0) >= args.min_confidence
              and str(e.get("value") or "").split("@")[0].lower() not in GENERIC_LOCALS
              and str(e.get("first_name") or "").strip()
              and STRONG_ROLE.search(str(e.get("position") or ""))
              and not WEAK_ROLE.search(str(e.get("position") or ""))]
    if not emails:
        returned = len(data.get("emails") or [])
        print(f"  {lead['company'][:20]:<20} no buyer-contact above {args.min_confidence} confidence "
              f"with a real name ({returned} contact(s) returned) — left for a human")
        continue
    best = sorted(emails, key=score, reverse=True)[0]
    name = " ".join(x for x in (best.get("first_name"), best.get("last_name")) if x).strip()
    role = ROLE_BY_DEPARTMENT.get(str(best.get("department") or "").lower(), "other")
    print(f"  {lead['company'][:20]:<20} -> {best.get('value')} | {name} | {best.get('position')} "
          f"| conf {best.get('confidence')} | dept {best.get('department')} -> role {role}")
    if args.apply:
        st = patch_lead(lead["id"], {"email": best.get("value"), "contact_name": name or None,
                                     "contact_title": best.get("position") or None,
                                     "contact_role": role,
                                     "note": f"contact enriched via Hunter domain-search ({domain})"})
        print(f"      crm write: {st}")

if not args.apply:
    print("\n  (dry run: nothing written. Re-run with --apply to fill the CRM.)")
