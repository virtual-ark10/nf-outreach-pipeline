#!/usr/bin/env python3
"""icp_research.py — the repeatable ICP-research pass: scan lookalike accounts, score them
against the ICP rubric, and (with --apply) import the strong + workable ones as CRM leads
for the existing enrichment + outreach chain to pick up.

This is the automated form of the one-off Evertrace lookalike study. It is a company-first
intake, exactly like scripts/import_candidates.py: a lead enters at stage 'leads' the moment
its domain is known, and the address is found afterwards by the free site scrape / treg
(scripts/discover_contacts.py) and Hunter (scripts/enrich_leads_hunter.py).

Data comes only from treg's FREE people lanes (quickenrich.people.search by company_url and
dropleads.people.search by companyDomains). No paid route is called, so a daily run costs $0.
Those lanes return real names, titles, LinkedIn URLs and firmographics, but no email — which is
why the lead lands with an empty address and the Hunter step fills it.

Config:
  icp/seeds.txt        seed domains, one per line, grouped by [segment]; "!" = never import
  icp/icp_spec.json    ICP dimensions, scoring weights and the import bands

Artifacts (rewritten each run) under --out (default /home/boxed/icp-research):
  leads_aggregated.json, leads_gtm_contacts.csv, icp_score_report.md, LEADS_brief.md
  (The narrative ICP.md is authored once and kept alongside; it is not regenerated.)

Usage
  python3 scripts/icp_research.py                    # scan + score + write artifacts, plan only
  python3 scripts/icp_research.py --apply --limit 40 # also import strong+workable as leads
  python3 scripts/icp_research.py --refresh          # ignore the 20h raw cache and re-scan
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEEDS = Path(os.environ.get("ICP_SEEDS", REPO / "icp" / "seeds.txt"))
SPEC = Path(os.environ.get("ICP_SPEC", REPO / "icp" / "icp_spec.json"))
NAMES = Path(os.environ.get("ICP_NAMES", REPO / "icp" / "names.json"))
OUT = Path(os.environ.get("ICP_OUT", "/home/boxed/icp-research"))
TREG = Path(os.environ.get("TREG_BIN", "/home/boxed/.local/bin/treg"))
ENV_FILE = Path(os.environ.get("NF_ENV_FILE", "/home/boxed/resend-pad/.env"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")
CACHE_TTL_H = float(os.environ.get("ICP_CACHE_TTL_H", "20"))

SIZE_CORE = (11, 200)
WEST = re.compile(r"(US|DK|UK|NL|DE|FR|EU|EE|CZ|AU|IL|CA|SE|NO|FI|ES|IT|IE|London|Copenhagen|"
                  r"Amsterdam|Tallinn|San Francisco|Palo Alto|New York)", re.I)
GTM_RE = re.compile(r"(growth|marketing|demand gen|revenue|gtm|go.to.market|sales|"
                    r"partnership|founder|ceo|chief executive|business development)", re.I)
NONGTM_RE = re.compile(r"(engineer|engineering|scientist|research|backend|frontend|devops|sre|"
                       r"designer|accountant|recruit|talent)", re.I)
ROLE_PATTERNS = [
    ("growth", re.compile(r"growth|gtm|go.to.market|demand", re.I)),
    ("marketing", re.compile(r"marketing|brand|content|communications", re.I)),
    ("cro", re.compile(r"revenue|sales|cro|business develop|partnership", re.I)),
    ("founder", re.compile(r"founder|ceo|chief exec|owner|managing", re.I)),
]


# --- config ----------------------------------------------------------------------

def parse_seeds(path: Path):
    """-> list of (domain, segment, importable)."""
    rows, segment = [], "signal_data"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[(\w+)\]$", line)
        if m:
            segment = m.group(1)
            continue
        if line.startswith("#"):
            continue
        anchor = line.startswith("!")
        domain = line.lstrip("!").strip().lower()
        if domain:
            rows.append((domain, segment, not anchor))
    seen, uniq = set(), []
    for d, s, imp in rows:
        if d in seen:
            continue
        seen.add(d)
        uniq.append((d, s, imp))
    return uniq


def load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_token() -> str:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAD_TOKEN=") or line.startswith("CRM_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get("CRM_TOKEN", "")


def norm(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def kebab(v) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")


# --- treg free lanes -------------------------------------------------------------

def treg_call(endpoint: str, payload: dict, timeout: int = 90):
    cmd = [str(TREG), "call", endpoint, "--method", "POST", "--data", json.dumps(payload)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    txt = re.sub(r"^treg:.*$", "", p.stdout or "", flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        return None


def fetch_domain(domain: str) -> list:
    people = []
    d = treg_call("quickenrich.people.search",
                  {"company_url": {"include": [domain]}, "per_page": 25})
    if d and isinstance(d.get("data"), list):
        for r in d["data"]:
            people.append({
                "full_name": f"{r.get('first_name','')} {r.get('last_name','')}".strip(),
                "title": r.get("title"), "linkedin": r.get("employee_linkedin"),
                "company": r.get("company_name"), "domain": domain,
                "employee_count": r.get("employee_count"), "revenue": r.get("revenue"),
                "industry": r.get("industry"), "city": r.get("city"),
                "country": r.get("country_code"), "src": "quickenrich",
            })
    d = treg_call("dropleads.people.search",
                  {"filters": {"companyDomains": [domain]},
                   "pagination": {"page": 1, "limit": 25}})
    for r in ((d or {}).get("data") or {}).get("leads") or []:
        people.append({
            "full_name": r.get("fullName"), "title": r.get("title"),
            "linkedin": r.get("linkedinUrl"), "company": r.get("companyName"),
            "domain": domain, "employee_count": r.get("companySize"), "revenue": None,
            "industry": r.get("industry"), "city": None, "country": None, "src": "dropleads",
        })
    # merge by linkedin / name|title
    merged = {}
    for p in people:
        k = (p.get("linkedin") or "").rstrip("/").lower() or \
            f"{str(p.get('full_name','')).lower()}|{str(p.get('title','')).lower()}"
        if not k:
            continue
        if k in merged:
            for f, v in p.items():
                if v and not merged[k].get(f):
                    merged[k][f] = v
        else:
            merged[k] = dict(p)
    return list(merged.values())


def scan(seeds, refresh: bool):
    cache_path = OUT / "people_raw.json"
    cache = {}
    if cache_path.exists() and not refresh:
        try:
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - blob.get("_ts", 0)
            if age < CACHE_TTL_H * 3600:
                cache = blob.get("domains", {})
                print(f"  raw cache is {age/3600:.1f}h old (< {CACHE_TTL_H}h): reusing "
                      f"{len(cache)} domain(s)")
        except Exception:
            cache = {}
    for domain, _seg, _imp in seeds:
        if domain in cache:
            continue
        people = fetch_domain(domain)
        cache[domain] = people
        print(f"  scanned {domain:<22} {len(people)} contact(s)", flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"_ts": time.time(), "domains": cache}, indent=2),
                              encoding="utf-8")
        time.sleep(0.2)
    return cache


# --- scoring ---------------------------------------------------------------------

def size_score(ec):
    if not ec:
        return 1
    nums = re.findall(r"\d+", str(ec).replace(",", ""))
    if not nums:
        return 1
    n = int(nums[0])
    if SIZE_CORE[0] <= n <= SIZE_CORE[1]:
        return 3
    if (200 < n <= 500) or (5 <= n < SIZE_CORE[0]):
        return 2
    if (500 < n <= 999) or n < 5:
        return 1
    return 0


def role_of(title: str = "") -> str:
    for role, pat in ROLE_PATTERNS:
        if pat.search(title or ""):
            return role
    return "other"


def build(seeds, raw, spec, names=None):
    names = names or {}
    seg_score = spec.get("segments", {})
    companies = []
    for domain, seg, importable in seeds:
        people = raw.get(domain, [])
        if not people:
            continue
        firm = {}
        for p in people:
            for k in ("employee_count", "revenue", "industry", "city", "country"):
                if p.get(k) and not firm.get(k):
                    firm[k] = p[k]
        gtm = [p for p in people
               if GTM_RE.search(p.get("title") or "") and not NONGTM_RE.search(p.get("title") or "")]
        gtm.sort(key=lambda p: ({"growth": 0, "marketing": 1, "cro": 2, "founder": 3}.get(
            role_of(p.get("title")), 4), str(p.get("full_name") or "")))
        name = names.get(domain) or next((p.get("company") for p in people if p.get("company")),
                                         domain)
        ind = seg_score.get(seg, 2)
        top = gtm[0].get("title") if gtm else ""
        buyer = 3 if re.search(r"growth|marketing|demand|gtm|go.to.market", top, re.I) else \
            2 if re.search(r"founder|ceo|chief exec|owner", top, re.I) else 1 if gtm else 0
        has_founder = any(re.search(r"founder|ceo|chief exec", (p.get("title") or ""), re.I)
                          for p in gtm)
        hq = ", ".join(x for x in (firm.get("city"), firm.get("country")) if x)
        acc = {
            "domain": domain, "company": name, "segment": seg, "importable": importable,
            "hq": hq, "employee_count": firm.get("employee_count"), "revenue": firm.get("revenue"),
            "industry": firm.get("industry"), "people_found": len(people),
            "gtm_contacts": gtm[:3],
            "scores": {
                "industry_fit": ind,
                "size_fit": size_score(firm.get("employee_count")),
                "geography": 3 if WEST.search(hq or "") else 2,
                "tech_stack_fit": 3 if ind == 3 else 2,
                "buyer_role_match": buyer,
                "trigger_event_present": 3 if firm.get("revenue") else 2,
                "budget_authority": 3 if (buyer == 3 or has_founder) else 2,
                "timeline": 2,
            },
        }
        acc["score"], acc["band"] = weighted(acc["scores"], spec["scoring_weights"])
        companies.append(acc)
    companies.sort(key=lambda c: (-c["score"], c["company"].lower()))
    return companies


def weighted(scores, weights):
    tw = sum(weights.values()) or 1
    tot = 0.0
    for k, w in weights.items():
        tot += (w / tw) * 100 * (max(0.0, min(1.0, scores.get(k, 0) / 3.0)))
    tot = round(tot, 1)
    band = "strong" if tot >= 80 else "workable" if tot >= 60 else "marginal" if tot >= 40 else "disqualify"
    return tot, band


# --- artifacts -------------------------------------------------------------------

def write_artifacts(companies, spec):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "leads_aggregated.json").write_text(
        json.dumps({"companies": companies,
                    "people_count": sum(c["people_found"] for c in companies)},
                   indent=2), encoding="utf-8")
    with open(OUT / "leads_gtm_contacts.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["company", "domain", "hq", "employee_count", "revenue", "segment",
                    "icp_score", "band", "contact_name", "contact_title", "linkedin"])
        for c in companies:
            if not c["gtm_contacts"]:
                w.writerow([c["company"], c["domain"], c["hq"], c["employee_count"],
                            c["revenue"], c["segment"], c["score"], c["band"], "", "", ""])
            for p in c["gtm_contacts"]:
                w.writerow([c["company"], c["domain"], c["hq"], c["employee_count"],
                            c["revenue"], c["segment"], c["score"], c["band"],
                            p.get("full_name"), p.get("title"), p.get("linkedin")])
    lines = [f"# ICP score report — {spec['icp_name']}", "",
             f"_generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", "",
             "| Company | Domain | Score | Band | Segment | People |",
             "|---------|--------|-------|------|---------|--------|"]
    for c in companies:
        lines.append(f"| {c['company']} | {c['domain']} | {c['score']} | {c['band']} | "
                     f"{c['segment']} | {c['people_found']} |")
    bands = {}
    for c in companies:
        bands[c["band"]] = bands.get(c["band"], 0) + 1
    lines += ["", f"**Bands:** " + " | ".join(f"{k} {v}" for k, v in sorted(bands.items()))]
    (OUT / "icp_score_report.md").write_text("\n".join(lines), encoding="utf-8")

    brief = [f"# Lead list — accounts matching the Evertrace-type ICP", "",
             f"_generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}; source: "
             f"free treg people-search lanes (quickenrich, dropleads)_", "",
             f"{len(companies)} companies with at least one person found; "
             f"{sum(c['people_found'] for c in companies)} people.", "",
             "Contact rule: GTM/go-to-market person first, founder/CEO as backup.", "",
             "| # | Company | Domain | HQ | Employees | Score | Band | First GTM contact | Title | LinkedIn |",
             "|---|---------|--------|----|-----------|-------|------|-------------------|-------|----------|"]
    for i, c in enumerate(companies, 1):
        top = c["gtm_contacts"][0] if c["gtm_contacts"] else {}
        li = (top.get("linkedin") or "").replace("http://www.", "https://www.")
        brief.append(f"| {i} | {c['company']} | {c['domain']} | {c['hq']} | "
                     f"{c['employee_count'] or '?'} | {c['score']} | {c['band']} | "
                     f"{top.get('full_name','')} | {top.get('title','')} | {li} |")
    (OUT / "LEADS_brief.md").write_text("\n".join(brief), encoding="utf-8")


# --- CRM import ------------------------------------------------------------------

def crm_call(method, path, token, body=None, tries=3):
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        req = urllib.request.Request(CRM + path, method=method, data=data,
                                     headers={"X-CRM-Token": token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except Exception:
                return e.code, {}
        except Exception as e:
            if i == tries - 1:
                return -1, {"error": str(e)}
            time.sleep(1 + i)
    return 429, {"error": "rate limited out"}


def lead_body(c):
    top = c["gtm_contacts"][0] if c["gtm_contacts"] else {}
    role = role_of(top.get("title"))
    prio = "HIGH" if c["band"] == "strong" else "MEDIUM"
    int_score = int(round(c["score"]))
    return {
        "id": kebab(c["company"]),
        "company": c["company"],
        "domain": c["domain"],
        "website": f"https://{c['domain']}",
        "source": "icp_research",
        "campaign": "icp-research",
        "priority": prio,
        "score": int_score,
        "industry": c.get("industry"),
        "city": c["hq"].split(",")[0].strip() if c.get("hq") else None,
        "contact_name": top.get("full_name") or None,
        "contact_title": top.get("title") or None,
        "contact_role": role,
        "tags": ["icp-research", c["band"], c["segment"]],
        "angle": f"data/signal company matching the Evertrace-type ICP ({c['band']})",
        "notes": [f"ICP research {time.strftime('%Y-%m-%d')}: {c['band']} ({c['score']}/100), "
                  f"segment {c['segment']}, {c['people_found']} contact(s) found via free "
                  f"people-search lanes; address to be filled by Hunter."],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ICP lookalike research -> CRM leads")
    ap.add_argument("--apply", action="store_true", help="import to the CRM (default: plan only)")
    ap.add_argument("--limit", type=int, default=60, help="max leads to import this run")
    ap.add_argument("--refresh", action="store_true", help="ignore the raw cache and re-scan")
    ap.add_argument("--min-band", default="workable", choices=["strong", "workable", "marginal"],
                    help="import this band or better (default workable)")
    args = ap.parse_args()

    if not SEEDS.exists() or not SPEC.exists():
        print(f"missing config: {SEEDS} or {SPEC} - aborting")
        return 1
    seeds = parse_seeds(SEEDS)
    spec = load_spec(SPEC)
    print(f"  {len(seeds)} seed domain(s); import bands {spec['bands']['import']}")

    raw = scan(seeds, args.refresh)
    names = {}
    if NAMES.exists():
        try:
            names = {k: v for k, v in json.loads(NAMES.read_text(encoding="utf-8")).items()
                     if not k.startswith("_")}
        except Exception:
            names = {}
    companies = build(seeds, raw, spec, names)
    write_artifacts(companies, spec)
    bands = {}
    for c in companies:
        bands[c["band"]] = bands.get(c["band"], 0) + 1
    print(f"  scored {len(companies)} account(s): "
          + " | ".join(f"{k} {v}" for k, v in sorted(bands.items())))
    print(f"  artifacts -> {OUT}")

    importable = [c for c in companies if c["importable"] and c["band"] in
                  ("strong", "workable") if _band_ok(c["band"], args.min_band)]
    print(f"  {len(importable)} lead(s) at {args.min_band}+ and importable")

    token = load_token()
    if not token:
        print("  no CRM token (PAD_TOKEN in the pad's .env) - cannot import")
        return 1
    code, payload = crm_call("GET", "/api/leads", token)
    if code != 200 or not isinstance(payload, dict):
        print(f"  cannot read the CRM: HTTP {code} - aborting import")
        return 1
    have = {norm(l.get("company")) for l in (payload.get("leads") or payload.get("data") or [])}
    print(f"  CRM holds {len(have)} lead(s)")

    added = skipped = failed = 0
    for c in importable[: args.limit]:
        if norm(c["company"]) in have:
            skipped += 1
            continue
        if not args.apply:
            print(f"  would import {c['company'][:26]:<26} {c['score']:>5} {c['band']:<9} "
                  f"{c['domain']}")
            continue
        code, resp = crm_call("POST", "/api/leads", token, lead_body(c))
        if code == 201:
            added += 1
            print(f"  added        {c['company'][:26]:<26} {c['score']:>5} {c['band']:<9} "
                  f"{c['domain']}")
        elif code == 409:
            skipped += 1
        else:
            failed += 1
            print(f"  FAILED       {c['company'][:26]:<26} HTTP {code} {resp.get('error','')}")
        time.sleep(0.15)

    print(f"\n  imported {added} | already present {skipped} | failed {failed}"
          f"{' | DRY RUN - nothing written' if not args.apply else ''}")
    return 0


def _band_ok(band, floor):
    order = {"strong": 2, "workable": 1, "marginal": 0, "disqualify": -1}
    return order.get(band, -1) >= order.get(floor, 1)


if __name__ == "__main__":
    sys.exit(main())
