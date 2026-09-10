#!/usr/bin/env python3
"""contact-discovery: fetch decision-maker candidates for a list of outreach targets via Hunter.io.

Usage:
    python3 hunter_contacts.py --key <API_KEY> --targets "Ahrefs, Brex, Tracksuit"
    python3 hunter_contacts.py --key <API_KEY> --targets file:targets.txt \
        --max-searches 20 --out /home/boxed/hunter-contacts.csv

Each name is resolved with domain-search (decision_maker=true, type=personal, limit=10 = free-plan max).
Verifies credit budget before and mid-run so it stops well short of exhausting the account.
Re-run with your own target list + key (the KEY is NOT stored; pass it each run).
"""
import argparse, csv, json, re, sys, time, urllib.parse, urllib.request

def api_get(base, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{base}?{qs}", headers={"User-Agent": "contact-discovery-skill"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

ACCOUNT = "https://api.hunter.io/v2/account"
DS = "https://api.hunter.io/v2/domain-search"

def credits(api_key):
    d = api_get(ACCOUNT, {"api_key": api_key})["data"]["requests"]
    return d["searches"]["remaining"], d["verifications"]["remaining"]

def lookup(api_key, company):
    """Return dict of domain search results for a company name (resolves domain)."""
    out = {"company": company, "domain": None, "org": None, "emails": []}
    try:
        j = api_get(DS, {"api_key": api_key, "company": company,
                         "type": "personal", "decision_maker": "true", "limit": 10})
        if "data" in j:
            out["domain"] = j["data"].get("domain")
            out["org"] = j["data"].get("organization")
            for e in j["data"].get("emails", []):
                out["emails"].append({
                    "email": e.get("value"),
                    "name": f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                    "position": e.get("position"),
                    "department": e.get("department"),
                    "confidence": e.get("confidence"),
                })
        elif "errors" in j:
            out["error"] = str(j["errors"])[:160]
    except Exception as ex:
        out["error"] = f"{type(ex).__name__}: {ex}"
    return out

def best(e_list):
    """Pick the best outreach contact by role: partnerships > growth > marketing > CRO/BD > founder."""
    if not e_list:
        return None
    prefs = ["partnership", "growth", "vp of marketing", "head of marketing",
             "director of marketing", "cmo", "chief revenue", "business development",
             "founder", "ceo"]
    def score(e):
        pos = (e.get("position") or "").lower()
        s = 0
        for i, kw in enumerate(prefs):
            if kw in pos:
                s += (len(prefs) - i) * 100
        s += (e.get("confidence") or 0)
        return s
    return max(e_list, key=score)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--targets", required=True, help="comma-sep names OR file:<path> (one per line)")
    ap.add_argument("--max-searches", type=int, default=25)
    ap.add_argument("--out", default="hunter-contacts.csv")
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()

    if a.targets.startswith("file:"):
        targets = [l.strip() for l in open(a.targets[5:]) if l.strip()]
    else:
        targets = [t.strip() for t in a.targets.split(",") if t.strip()]

    rem, vrem = credits(a.key)
    print(f"budget before: {rem} searches, {vrem} verifications  | targets={len(targets)} max={a.max_searches}")
    if rem <= 0:
        sys.exit("NO search credits — stop. Get a fresh account/key first.")

    rows, used = [], 0
    for t in targets:
        if used >= min(a.max_searches, rem):
            print("budget cap hit — stopping early"); break
        print(f"  -> {t} ...")
        res = lookup(a.key, t)
        used += 1
        pick = best(res["emails"])
        rows.append({
            "sponsor": t, "domain": res.get("domain"), "org": res.get("org"),
            "contact_name": pick["name"] if pick else "",
            "position": pick["position"] if pick else "",
            "department": pick["department"] if pick else "",
            "contact_email": pick["email"] if pick else "",
            "confidence": pick["confidence"] if pick else "",
            "error": res.get("error", ""),
        })
        time.sleep(a.sleep)

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    rem2, _ = credits(a.key)
    print(f"\nwrote {a.out}  ({len(rows)} rows) | searches left: {rem2}")
    print("REMEMBER: emails are Hunter-inferred leads. Watch for wrong-company auto-resolution;")
    print("re-run ambiguous names with explicit domain= (see references/hunter-api.md).")

if __name__ == "__main__":
    main()