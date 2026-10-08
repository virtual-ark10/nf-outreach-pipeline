#!/usr/bin/env python3
"""icp_research.py — the repeatable ICP-research pass: DISCOVER lookalike accounts
from the Diffbot Knowledge Graph by the ICP bands, score them against the ICP
rubric, and (with --apply) import the strong + workable ones as CRM leads for the
existing enrichment + outreach chain to pick up.

Seeding is automated. The old hand-curated icp/seeds.txt is gone: candidates come
from a type:Organization DQL query built from icp/icp_spec.json's `discovery` block
(employee band, founding-date window, funding band, geography, and the category set
per segment). The Diffbot firmographics (employees, funding, founding date, country,
category) also drive the scoring, so a candidate is scored on the KG record rather
than on whatever the people-search lanes happened to return.

Entity-resolution rules, pinned here (they are the difference between a right answer
and a confident wrong one):
  * Never query by domain alone. In discovery the record IS linked, but any org
    lookup elsewhere must constrain name AND domain (see the diffbot skills).
  * Never surface Diffbot's free-text `summary`/`description`. It is not requested
    and not written to any artifact. Headline/numeric fields only.
  * Sort and rank; never take the first row. Candidates are ranked by `importance`
    then `nbEmployees` (the KG holds stale dupes and regional sub-entities).
  * Optionally gate on the NLP resolver confidence (`--verify`): `db entities`
    returns a 0-1 Confidence; entities with no Diffbot ID are treated as unlinked.

Contacts still come only from treg's FREE people lanes (quickenrich, dropleads) plus
ONE free-tier Diffbot DQL call for discovery, so a daily run stays at $0. The lanes
return real names, titles, LinkedIn URLs and firmographics but no email, which is why
the lead lands with an empty address and Hunter fills it.

Config:
  icp/icp_spec.json    ICP dimensions, scoring weights, import bands, and the
                       `discovery` block (bands, geo, per-segment categories)
  icp/names.json       optional display-name overrides (keyed by domain)

Artifacts (rewritten each run) under --out (default /home/boxed/icp-research):
  leads_aggregated.json, leads_gtm_contacts.csv, icp_score_report.md, LEADS_brief.md

Usage
  python3 scripts/icp_research.py                    # discover + score, plan only
  python3 scripts/icp_research.py --apply --limit 40 # also import strong+workable
  python3 scripts/icp_research.py --pool 20          # cap how many candidates to pull
  python3 scripts/icp_research.py --verify           # add the NLP confidence gate
  python3 scripts/icp_research.py --refresh          # ignore the 20h contact cache
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
SPEC = Path(os.environ.get("ICP_SPEC", REPO / "icp" / "icp_spec.json"))
NAMES = Path(os.environ.get("ICP_NAMES", REPO / "icp" / "names.json"))
OUT = Path(os.environ.get("ICP_OUT", "/home/boxed/icp-research"))
TREG = Path(os.environ.get("TREG_BIN", "/home/boxed/.local/bin/treg"))
DB = Path(os.environ.get("DIFFBOT_BIN", Path.home() / ".diffbot/venv/bin/db"))
DIFFBOT_CREDS = Path(os.environ.get("DIFFBOT_CREDENTIALS", Path.home() / ".diffbot/credentials"))
ENV_FILE = Path(os.environ.get("NF_ENV_FILE", "/home/boxed/resend-pad/.env"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")
CACHE_TTL_H = float(os.environ.get("ICP_CACHE_TTL_H", "20"))
DQL_URL = "https://kg.diffbot.com/kg/v3/dql"
DQL_FIELDS = ("name,homepageUri,nbEmployees,foundingDate,totalInvestment,"
              "categories,location,importance")

SIZE_CORE = (11, 200)
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

def load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_token() -> str:
    """CRM token from the pad's .env."""
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


# --- Diffbot discovery -----------------------------------------------------------

def load_diffbot_token() -> str:
    env = os.environ.get("DIFFBOT_API_TOKEN")
    if env:
        return env.strip()
    try:
        for line in DIFFBOT_CREDS.read_text(encoding="utf-8").splitlines():
            if line.startswith("DIFFBOT_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def build_discovery_query(spec: dict) -> str:
    """type:Organization DQL from the spec's discovery bands + per-segment categories."""
    d = spec["discovery"]
    b = d["bands"]
    cats = [c for seg in d["segments"].values() for c in seg]
    q = "type:Organization categories.name:or(" + ",".join(json.dumps(c) for c in cats) + ")"
    q += f' range:nbEmployees:{b["employees"][0]}-{b["employees"][1]}'
    q += " location.country.name:or(" + ",".join(json.dumps(c) for c in d["geo_countries"]) + ")"
    q += f' foundingDate>="{b["founded"][0]}" foundingDate<="{b["founded"][1]}"'
    q += f' totalInvestment.value>={b["funding_usd"][0]} totalInvestment.value<={b["funding_usd"][1]}'
    return q


def normalize_domain(uri) -> str:
    if not uri:
        return ""
    u = str(uri).strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.split("/", 1)[0]
    u = u.split("?", 1)[0]
    u = re.sub(r"^www\.", "", u)
    return u if "." in u and len(u) >= 4 else ""


def segment_of(categories, spec: dict) -> str:
    """Map a record's Diffbot categories to an ICP segment. Highest score wins
    (signal_data 3 > enrichment 2 > infra 1); default signal_data."""
    seg_map = spec["discovery"]["segments"]
    names = {str(c.get("name") if isinstance(c, dict) else c) for c in (categories or [])}
    best, best_score = "signal_data", -1
    for seg, cats in seg_map.items():
        score = spec["segments"].get(seg, 0)
        if names & set(cats) and score > best_score:
            best, best_score = seg, score
    return best


def _diffbot_dql(dql: str, size: int, token: str):
    url = DQL_URL + "?" + urllib.parse.urlencode(
        {"token": token, "query": dql, "size": str(size), "get": DQL_FIELDS})
    req = urllib.request.Request(url, headers={"accept": "application/json",
                                               "User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def entity_confidence(name: str) -> float:
    """NLP-resolver confidence (0-1) for a name, or -1 when unavailable/unlinked.
    Uses the diffbot CLI (`db entities`). Best-effort; never raises."""
    if not DB.exists() or not name:
        return -1.0
    try:
        p = subprocess.run([str(DB), "entities", name, "-f", "json"],
                           capture_output=True, text=True, timeout=60)
        d = json.loads(p.stdout or "{}")
    except Exception:
        return -1.0
    ents = d.get("entities") or d.get("data") or []
    best = -1.0
    for e in ents:
        if not isinstance(e, dict):
            continue
        if str(e.get("type") or "").lower() != "organization":
            continue
        if not (e.get("diffbotUri") or e.get("id")):  # unlinked -> not a KG record
            continue
        c = e.get("confidence")
        if isinstance(c, (int, float)):
            best = max(best, float(c))
    return best


def discover(spec: dict, pool: int, verify: bool = False):
    """-> (seeds, firm). seeds = [(domain, segment, True)]; firm = {domain: {..}}.

    One DQL call, ranked by importance then employees, deduped by domain, with the
    free-text summary never requested. Returns ([], {}) on any API failure so the
    caller can report it plainly instead of importing nothing silently.
    """
    token = load_diffbot_token()
    if not token:
        print("  no Diffbot token (~/.diffbot/credentials) - cannot discover")
        return [], {}
    dql = build_discovery_query(spec)
    try:
        d = _diffbot_dql(dql, max(1, min(pool, 250)), token)
    except Exception as e:
        print(f"  Diffbot discovery failed: {e}")
        return [], {}
    recs = []
    for row in (d.get("data") or []):
        e = row.get("entity") or {}
        domain = normalize_domain(e.get("homepageUri"))
        name = str(e.get("name") or "").strip()
        if not domain or not name:
            continue
        cats = [c.get("name") if isinstance(c, dict) else c for c in (e.get("categories") or [])]
        imp = e.get("importance")
        recs.append({
            "domain": domain,
            "company": name,
            "segment": segment_of(cats, spec),
            "employee_count": e.get("nbEmployees"),
            "country": ((e.get("location") or {}).get("country") or {}).get("name"),
            "city": ((e.get("location") or {}).get("city") or {}).get("name"),
            "industry": (cats[0] if cats else None),
            "funding": (e.get("totalInvestment") or {}).get("value"),
            "founded": (e.get("foundingDate") or {}).get("str", "").lstrip("d"),
            "importance": imp if isinstance(imp, (int, float)) else 0.0,
        })
    # rank: importance, then headcount; never take the first row blindly
    recs.sort(key=lambda r: (-r["importance"], -(r["employee_count"] or 0)))
    seen, out = set(), []
    for r in recs:
        if r["domain"] in seen:
            continue
        seen.add(r["domain"])
        out.append(r)
    if verify:
        keep = []
        minc = spec["discovery"].get("min_entity_confidence", 0.6)
        for r in out:
            c = entity_confidence(r["company"])
            if c < 0 or c >= minc:      # unlinked -1 keeps the record (KG already matched it)
                keep.append(r)
            else:
                print(f"  drop {r['company']:<28} confidence {c:.2f} < {minc}")
            time.sleep(13)              # free tier: 5 requests/minute
        out = keep
    seeds = [(r["domain"], r["segment"], True) for r in out]
    firm = {r["domain"]: {
        "company": r["company"], "employee_count": r["employee_count"],
        "country": r["country"], "city": r["city"], "industry": r["industry"],
        "funding": r["funding"], "founded": r["founded"], "importance": r["importance"],
        "segment": r["segment"],
    } for r in out}
    return seeds, firm


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
    for entry in seeds:
        domain = entry[0]
        if domain in cache:
            continue
        people = fetch_domain(domain)
        if not people:
            # Never cache an empty scan: treg saturates (503 "retry in a moment") and a
            # provider gap looks identical to a company with no contacts. Caching the miss
            # would serve nothing for the full TTL; leaving it out retries next run.
            print(f"  scanned {domain:<22} 0 contact(s) (not cached, will retry)", flush=True)
            continue
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


def build(seeds, raw, spec, names=None, firm=None):
    names = names or {}
    firm = firm or {}
    seg_score = spec.get("segments", {})
    geo = {c.lower() for c in spec.get("discovery", {}).get("geo_countries", [])}
    companies = []
    for entry in seeds:
        domain, seg, importable = entry[0], entry[1], entry[2]
        people = raw.get(domain, [])
        if not people:
            continue
        f = firm.get(domain, {})

        def pick(k):
            """Diffbot firmographic first (the KG record), then the people lanes."""
            v = f.get(k)
            if v not in (None, "", [], {}):
                return v
            return next((p.get(k) for p in people if p.get(k)), None)

        employee_count = pick("employee_count")
        revenue = pick("revenue")
        industry = pick("industry")
        country = pick("country")
        city = pick("city")
        funding = f.get("funding")
        gtm = [p for p in people
               if GTM_RE.search(p.get("title") or "") and not NONGTM_RE.search(p.get("title") or "")]
        gtm.sort(key=lambda p: ({"growth": 0, "marketing": 1, "cro": 2, "founder": 3}.get(
            role_of(p.get("title")), 4), str(p.get("full_name") or "")))
        name = f.get("company") or names.get(domain) or \
            next((p.get("company") for p in people if p.get("company")), domain)
        ind = seg_score.get(seg, 2)
        top = gtm[0].get("title") if gtm else ""
        buyer = 3 if re.search(r"growth|marketing|demand|gtm|go.to.market", top, re.I) else \
            2 if re.search(r"founder|ceo|chief exec|owner", top, re.I) else 1 if gtm else 0
        has_founder = any(re.search(r"founder|ceo|chief exec", (p.get("title") or ""), re.I)
                          for p in gtm)
        hq = ", ".join(x for x in (city, country) if x)
        acc = {
            "domain": domain, "company": name, "segment": seg, "importable": importable,
            "hq": hq, "employee_count": employee_count, "revenue": revenue,
            "industry": industry, "people_found": len(people),
            "funding": funding, "founded": f.get("founded"),
            "gtm_contacts": gtm[:3],
            "scores": {
                "industry_fit": ind,
                "size_fit": size_score(employee_count),
                "geography": 3 if (country or "").lower() in geo else 2,
                "tech_stack_fit": 3 if ind == 3 else 2,
                "buyer_role_match": buyer,
                "trigger_event_present": 3 if funding else 2,
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
        w.writerow(["company", "domain", "hq", "employee_count", "funding", "founded", "segment",
                    "icp_score", "band", "contact_name", "contact_title", "linkedin"])
        for c in companies:
            if not c["gtm_contacts"]:
                w.writerow([c["company"], c["domain"], c["hq"], c["employee_count"],
                            c.get("funding"), c.get("founded"), c["segment"], c["score"],
                            c["band"], "", "", ""])
            for p in c["gtm_contacts"]:
                w.writerow([c["company"], c["domain"], c["hq"], c["employee_count"],
                            c.get("funding"), c.get("founded"), c["segment"], c["score"],
                            c["band"], p.get("full_name"), p.get("title"), p.get("linkedin")])
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
             f"Diffbot KG discovery by ICP bands + free treg people-search lanes_", "",
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
        "angle": f"data/signal company matched to the Evertrace-type ICP ({c['band']})",
        "notes": [f"ICP research {time.strftime('%Y-%m-%d')}: {c['band']} ({c['score']}/100), "
                  f"segment {c['segment']}, discovered via Diffbot KG by the ICP bands "
                  f"(emp {c.get('employee_count')}, funding {c.get('funding')}), "
                  f"{c['people_found']} contact(s) via free people-search lanes; "
                  f"address to be filled by Hunter."],
    }


def _band_ok(band, floor):
    order = {"strong": 2, "workable": 1, "marginal": 0, "disqualify": -1}
    return order.get(band, -1) >= order.get(floor, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="ICP lookalike discovery (Diffbot) -> CRM leads")
    ap.add_argument("--apply", action="store_true", help="import to the CRM (default: plan only)")
    ap.add_argument("--limit", type=int, default=60, help="max NEW leads to import this run")
    ap.add_argument("--pool", type=int, default=0, help="max candidates to discover (0 = spec pool)")
    ap.add_argument("--verify", action="store_true", help="gate candidates on NLP-resolver confidence")
    ap.add_argument("--refresh", action="store_true", help="ignore the raw cache and re-scan")
    ap.add_argument("--min-band", default="workable", choices=["strong", "workable", "marginal"],
                    help="import this band or better (default workable)")
    args = ap.parse_args()

    if not SPEC.exists():
        print(f"missing config: {SPEC} - aborting")
        return 1
    spec = load_spec(SPEC)
    pool = args.pool or int(spec.get("discovery", {}).get("pool", 250))
    print(f"  discovering candidates from the Diffbot KG (pool {pool}); "
          f"import bands {spec['bands']['import']}")

    seeds, firm = discover(spec, pool, verify=args.verify)
    print(f"  discovered {len(seeds)} candidate domain(s)")
    if not seeds:
        print("  nothing discovered (Diffbot returned no candidates) - aborting")
        return 1

    raw = scan(seeds, args.refresh)
    names = {}
    if NAMES.exists():
        try:
            names = {k: v for k, v in json.loads(NAMES.read_text(encoding="utf-8")).items()
                     if not k.startswith("_")}
        except Exception:
            names = {}
    companies = build(seeds, raw, spec, names, firm)
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

    added = skipped = failed = attempted = 0
    for c in importable:
        if norm(c["company"]) in have:
            skipped += 1
            continue
        # --limit caps NEW leads attempted this run, so it is counted against attempts, not
        # against the position in the list. Slicing (importable[:limit]) let accounts already
        # in the CRM eat the whole budget: of 88 importable where 49 were present, only 11
        # slots were left and everything below them was never examined at all.
        if attempted >= args.limit:
            print(f"  limit of {args.limit} new lead(s) reached; "
                  f"{len(importable) - skipped - attempted} left unexamined")
            break
        if not args.apply:
            print(f"  would import {c['company'][:26]:<26} {c['score']:>5} {c['band']:<9} "
                  f"{c['domain']}")
            attempted += 1
            continue
        attempted += 1
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


if __name__ == "__main__":
    sys.exit(main())
