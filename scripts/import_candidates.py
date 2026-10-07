#!/usr/bin/env python3
"""import_candidates.py — the corpus export's qualified sponsors become CRM leads.

Why this exists: the intake job could only add a company it already had an address for,
so 113 qualified candidates sat in the export while the uncontacted pool ran dry at 15
leads against a 50-a-day cap. A candidate company is worth adding the moment its domain is
known, because the address is found afterwards by three engines, all of which work on a CRM
row: the free site scrape and treg (scripts/discover_contacts.py) and Hunter
(scripts/enrich_leads_hunter.py, 10 domains a day).

Domain resolution is a search, not a guess. Guessing brand.com and checking that the page
says "brand" cannot tell Apollo.io from Apollo Global Management, and on the first run it
put Anam at anam.com when the sponsor is anam.ai. So the brand's own name goes to the search
route (treg.google.serp.organic, $0.0005 a call), the result hosts are filtered to the ones
whose registrable name IS the brand, and the winner has to name the brand on its own page.
The free guess-and-check path stays as a fallback for when the search route is unavailable.

Nothing is invented: every field comes from the export row (score, quality, placements,
publications, angle, categories) or from the corpus's own all-sponsors.json (the slug).

  python3 scripts/import_candidates.py                        # dry run, print the plan
  python3 scripts/import_candidates.py --apply --limit 40
  python3 scripts/import_candidates.py --apply --recheck       # correct guessed domains
  python3 scripts/import_candidates.py --min-quality HIGH --min-placements 2
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
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPORT_CSV = Path(os.environ.get(
    "NF_EXPORT_CSV", "/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv"))
ALL_SPONSORS = Path(os.environ.get(
    "NF_ALL_SPONSORS", "/srv/newsletterfit/reports/sponsor-outreach/all-sponsors.json"))
ENV_FILE = Path(os.environ.get("NF_ENV_FILE", "/home/boxed/resend-pad/.env"))
USAGE_FILE = Path(os.environ.get("NF_TREG_USAGE", REPO / "data" / "treg-usage.json"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")

TREG = Path(os.environ.get("TREG_BIN", "/home/boxed/.local/bin/treg"))
SERP_ROUTE = "treg.google.serp.organic"
SERP_EXCLUDE = os.environ.get("TREG_EXCLUDE", "serpapi")
SERP_MAX_COST = os.environ.get("TREG_MAX_COST", "0.01")

QUALITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
# Only used when the search route is unavailable: a brand's site is reached by its own name
# far more often than by a prefix or a suffix.
DOMAIN_SUFFIXES = ("com", "io", "ai", "co", "app", "dev", "org", "net")
DOMAIN_PREFIXES = ("get", "try", "use")
DOMAIN_SUFFIX_WORDS = ("hq", "app")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# The brand/page/host matching rules live in the local-SEO pipeline's site_match.py and are
# imported rather than reimplemented: that module is already the one place either pipeline
# asks "is this the brand's own site", and a second copy is a second thing to get wrong.
SL_REPO = Path(os.environ.get("SL_REPO", "/home/boxed/local-seo-leadgen"))
if str(SL_REPO) not in sys.path:
    sys.path.insert(0, str(SL_REPO))
try:
    from site_match import (label_is_brand, norm_domain, page_names_brand,  # noqa: E402
                            registrable)
except ImportError as e:
    print(f"cannot import the shared brand rules from {SL_REPO} ({e}) - refusing to run")
    sys.exit(1)


# --- plumbing --------------------------------------------------------------------

def load_token() -> str:
    """The CRM token, from the pad's env file. Never printed."""
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAD_TOKEN=") or line.startswith("CRM_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get("CRM_TOKEN", "")


def norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def kebab(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def crm_call(method: str, path: str, token: str, body=None, tries: int = 3):
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


def split_list(value) -> list:
    """The export writes publication lists human-joined ("A, B, and C"), not as JSON."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = re.sub(r",\s*and\s+", ", ", str(value))
    return [t.strip().strip('"') for t in re.split(r",| and ", text) if t.strip()]


def month_totals() -> dict:
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get(datetime.now().strftime("%Y-%m")) or {}


def record_serps(cost: float, calls: int) -> None:
    if not calls:
        return
    data = {}
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    month = data.setdefault(datetime.now().strftime("%Y-%m"), {})
    month["serp_usd"] = round(float(month.get("serp_usd", 0.0)) + cost, 6)
    month["serps"] = int(month.get("serps", 0)) + calls
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- resolving a company name to its own site ------------------------------------

def fetch(url: str, timeout: int = 12) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(400_000).decode("utf-8", "ignore")
    except Exception:
        return ""


def serp_hosts(name: str, limit: int = 8) -> tuple:
    """-> (hosts in rank order, cost) from the search route. Never raises."""
    body = {"q": name, "gl": "us", "hl": "en", "limit": limit}
    cmd = [str(TREG), "call", SERP_ROUTE, "--method", "POST",
           "--header", f"X-Treg-Route-Exclude: {SERP_EXCLUDE}",
           "--header", f"X-Treg-Route-Max-Cost: {SERP_MAX_COST}",
           "--data", json.dumps(body)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return [], 0.0
    m = re.search(r"charged \$([0-9.]+)", (p.stdout or "") + (p.stderr or ""))
    cost = float(m.group(1)) if m else 0.0
    body_txt = re.sub(r"^treg:.*$", "", p.stdout or "", flags=re.M).strip()
    try:
        results = (json.loads(body_txt).get("output") or {}).get("results") or []
    except Exception:
        return [], cost
    hosts = []
    for r in results:
        host = registrable(urllib.parse.urlparse(str(r.get("link") or "")).netloc)
        if host and host not in hosts:
            hosts.append(host)
    return hosts, cost


def guesses_for(name: str, slug: str = "") -> list:
    base = re.sub(r"[^a-z0-9]", "", (slug or kebab(name)).lower())
    if not base:
        return []
    out = [f"{base}.{s}" for s in DOMAIN_SUFFIXES]
    out += [f"{p}{base}.com" for p in DOMAIN_PREFIXES]
    out += [f"{base}{w}.com" for w in DOMAIN_SUFFIX_WORDS]
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def resolve_domain(name: str, slug: str, budget: dict) -> tuple:
    """-> (domain, evidence). Search first; guess-and-check only when the route is dry.

    The guess fallback runs only when the search returned no host that carries the brand's
    own name. If the search answered and named the brand's sites but none of them passed the
    page check, guessing further is how a namesake gets imported, so it stops there.
    """
    if budget.get("serps_left", 0) > 0:
        budget["serps_left"] -= 1
        budget["calls"] = budget.get("calls", 0) + 1
        hosts, cost = serp_hosts(name)
        budget["cost"] = budget.get("cost", 0.0) + cost
        record_serps(cost, 1)               # spend is recorded as it happens, not at the end
        branded = [h for h in hosts if label_is_brand(h, name, slug)]
        for host in branded:
            if page_names_brand(fetch(f"https://{host}/"), name):
                return host, f"search result {host} names the brand"
        if branded:
            return "", ""
    for domain in guesses_for(name, slug)[:5]:
        html = fetch(f"https://{domain}/", timeout=8)
        if html and label_is_brand(domain, name, slug) and page_names_brand(html, name):
            return domain, f"guessed {domain} names the brand"
        time.sleep(0.3)
    return "", ""


def main() -> int:
    ap = argparse.ArgumentParser(description="import qualified corpus sponsors as CRM leads")
    ap.add_argument("--export", default=str(EXPORT_CSV))
    ap.add_argument("--all-sponsors", default=str(ALL_SPONSORS))
    ap.add_argument("--min-quality", default="MEDIUM", choices=list(QUALITY_ORDER),
                    help="HIGH, MEDIUM or LOW; the rule is this quality or better")
    ap.add_argument("--min-placements", type=int, default=1,
                    help="corpus placements a candidate must have (default 1)")
    ap.add_argument("--limit", type=int, default=40, help="candidates to import this run")
    ap.add_argument("--max-serps", type=int, default=120, help="name lookups this run")
    ap.add_argument("--serp-monthly-cap", type=float, default=0.25,
                    help="stop searching once this month's search spend reaches this")
    ap.add_argument("--recheck", action="store_true",
                    help="re-resolve the domains of untouched intake leads already imported")
    ap.add_argument("--apply", action="store_true", help="write to the CRM (default: plan only)")
    args = ap.parse_args()

    token = load_token()
    if not token:
        print("no CRM token (PAD_TOKEN in the pad's .env) - aborting")
        return 1

    budget = {"serps_left": max(0, args.max_serps), "cost": 0.0, "calls": 0}
    spent = float(month_totals().get("serp_usd", 0.0))
    if spent >= args.serp_monthly_cap:
        budget["serps_left"] = 0
        print(f"  search spend this month ${spent:.4f} is at the ${args.serp_monthly_cap:.2f} "
              f"cap: falling back to guess-and-check")

    code, payload = crm_call("GET", "/api/leads", token)
    if code != 200:
        print(f"cannot read the CRM: HTTP {code} {payload.get('error', '')} - aborting")
        return 1
    existing = payload.get("leads") or payload.get("data") or []
    have_companies = {norm(l.get("company")) for l in existing}
    print(f"  CRM: {len(existing)} lead(s)")

    slugs = {}
    try:
        for s in json.loads(Path(args.all_sponsors).read_text(encoding="utf-8")):
            key = norm(s.get("sponsor"))
            if key:
                slugs[key] = str(s.get("slug") or s.get("key") or "").strip()
    except Exception:
        pass

    # --recheck: a domain that came from a guess is worth a second look, because a guess
    # cannot tell a namesake from the sponsor. Only leads nobody has written to are touched.
    if args.recheck:
        wrong = fixed = checked = 0
        for l in existing:
            if checked >= args.limit:
                break
            if str(l.get("source")) != "intake" or str(l.get("stage")) != "leads":
                continue
            if str(l.get("email") or "").strip() or str(l.get("first_contact_at") or "").strip():
                continue
            name = str(l.get("company") or "").strip()
            current = str(l.get("domain") or "").strip().lower()
            if not name:
                continue
            checked += 1
            domain, evidence = resolve_domain(name, slugs.get(norm(name), ""), budget)
            if not domain:
                print(f"  unresolved {name} (kept {current or '-'})")
                continue
            if domain == current:
                continue
            wrong += 1
            print(f"  {'fixed' if args.apply else 'would fix'} {name:<26} "
                  f"{current or '-':<24} -> {domain} ({evidence})")
            if args.apply:
                code, resp = crm_call("PATCH", f"/api/leads/{urllib.parse.quote(str(l.get('id')))}",
                                      token, {"domain": domain, "website": f"https://{domain}"})
                if code in (200, 201):
                    fixed += 1
                    crm_call("POST", f"/api/leads/{urllib.parse.quote(str(l.get('id')))}/note",
                             token, {"body": f"domain corrected by search: {current or '-'} -> "
                                             f"{domain} ({evidence})"})
                else:
                    print(f"    FAILED {name}: HTTP {code} {resp.get('error', '')}")
        record_serps(budget["cost"], budget["calls"])
        print(f"\nrecheck: {wrong} domain(s) wrong, {fixed} corrected, "
              f"${budget['cost']:.4f} in name lookups"
              f"{'' if args.apply else ' | DRY RUN - nothing written'}")
        return 0

    if not Path(args.export).exists():
        print(f"no export at {args.export} - aborting")
        return 1
    rows = []
    with open(args.export, newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)

    floor = QUALITY_ORDER[args.min_quality]
    candidates = []
    for r in rows:
        q = str(r.get("outreachQuality") or "").strip().upper()
        try:
            placements = int(float(r.get("placements") or 0))
        except (TypeError, ValueError):
            placements = 0
        name = str(r.get("sponsor") or "").strip()
        if not name or QUALITY_ORDER.get(q, -1) < floor or placements < args.min_placements:
            continue
        if norm(name) in have_companies:
            continue
        candidates.append((placements, q, name, r))
    candidates.sort(key=lambda t: (-QUALITY_ORDER.get(t[1], -1), -t[0], t[2].lower()))
    print(f"  export: {len(rows)} row(s); {len(candidates)} qualified candidate(s) not in "
          f"the CRM at {args.min_quality}+ with {args.min_placements}+ placement(s)")

    added, unresolved, failed = [], [], []
    for placements, quality, name, r in candidates[: args.limit]:
        slug = slugs.get(norm(name), "")
        domain, evidence = resolve_domain(name, slug, budget)
        if not args.apply:
            print(f"  would import {name:<28} ({quality}, {placements} placement(s))")
            print(f"      domain {domain or 'UNRESOLVED':<28} {evidence or 'no match'}")
            continue
        if not domain:
            unresolved.append(name)
            print(f"  no domain  {name} - skipped (nothing to enrich or pitch)")
            continue
        body = {
            "id": kebab(name),
            "company": name,
            "domain": domain,
            "website": f"https://{domain}",
            "source": "intake",
            "priority": quality,
            "score": r.get("sponsorScore") or None,
            "angle": r.get("bestOutreachAngle") or None,
            "sponsored_pubs": split_list(r.get("topSponsoredPublications")),
            "recommended_pubs": split_list(r.get("topRecommendedPublications")),
            "subscriber_range": r.get("subscriberRange") or None,
            "notes": [f"corpus export {time.strftime('%Y-%m-%d')}: {quality}, {placements} "
                      f"placement(s); domain resolved by {evidence}"],
        }
        code, resp = crm_call("POST", "/api/leads", token, body)
        if code == 201:
            added.append((name, domain))
            print(f"  added      {name:<28} {domain:<28} {quality} {placements} placement(s)")
        elif code == 409:
            print(f"  exists     {name:<28} {domain}")
        else:
            failed.append((name, code, resp.get("error", "")))
            print(f"  FAILED     {name:<28} HTTP {code} {resp.get('error', '')}")
        time.sleep(0.2)

    record_serps(budget["cost"], budget["calls"])
    print(f"\nimported {len(added)} | unresolved {len(unresolved)} | failed {len(failed)} | "
          f"search spend ${budget['cost']:.4f}"
          f"{' | DRY RUN - nothing written' if not args.apply else ''}")
    if unresolved:
        print(f"  no domain found for: {', '.join(unresolved[:10])}"
              f"{' ...' if len(unresolved) > 10 else ''}")
    if args.apply:
        code, payload = crm_call("GET", "/api/leads", token)
        if code == 200:
            pool = [l for l in (payload.get("leads") or payload.get("data") or [])
                    if str(l.get("stage")) == "leads"
                    and not str(l.get("first_contact_at") or "").strip()]
            with_addr = [l for l in pool if str(l.get("email") or "").strip()]
            print(f"  uncontacted pool now {len(pool)} lead(s), {len(with_addr)} with an address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
