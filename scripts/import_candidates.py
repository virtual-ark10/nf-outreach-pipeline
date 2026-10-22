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

Ordering: candidates are imported preferred tier first (MEDIUM ahead of HIGH, see
scripts/_lead_rank.py), then by placement count. --min-quality is a threshold, not an order:
"MEDIUM" means MEDIUM or better.

  python3 scripts/import_candidates.py                        # dry run, print the plan
  python3 scripts/import_candidates.py --apply --limit 40
  python3 scripts/import_candidates.py --apply --recheck       # correct guessed domains
  python3 scripts/import_candidates.py --min-quality HIGH --min-placements 2

Operator escapes, for a domain the search gets wrong or cannot settle:
  --skip "Liberty Capital"                    leave a candidate out of this run
  --pin-domain "Affinity=affinity.co"          import at a domain a human confirmed
  data/intake-hold.txt                        durable holds, every run (default --hold-file)
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
HOLD_FILE = Path(os.environ.get("NF_INTAKE_HOLD", REPO / "data" / "intake-hold.txt"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")

TREG = Path(os.environ.get("TREG_BIN", "/home/boxed/.local/bin/treg"))
SERP_ROUTE = "treg.google.serp.organic"
SERP_EXCLUDE = os.environ.get("TREG_EXCLUDE", "serpapi")
SERP_MAX_COST = os.environ.get("TREG_MAX_COST", "0.01")

# Lead quality preference lives in one place (scripts/_lead_rank.py). The intake leads with the
# preferred tier too, so the unimported pool does not fill HIGH-first while every downstream
# queue works MEDIUM-first.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icp_fit                                          # noqa: E402
from _lead_rank import quality_rank                    # noqa: E402

# Threshold scale only (LOW < MEDIUM < HIGH), used by --min-quality and the "or better" floor.
# This is NOT the work order: quality_rank() decides which tier is imported first.
QUALITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
# Only used when the search route is unavailable: a brand's site is reached by its own name
# far more often than by a prefix or a suffix.
DOMAIN_SUFFIXES = ("com", "io", "ai", "co", "app", "dev", "org", "net")
DOMAIN_PREFIXES = ("get", "try", "use")
DOMAIN_SUFFIX_WORDS = ("hq", "app")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# Free host lookup, OFF by default and deliberately so. The local SearXNG looked like the
# right free rung, but its aggregate route returns zero results on this host and its bing
# engine, under load, returned unrelated adult sites for a mattress brand ("VelaSleep" ->
# xhamster.com, 2026-10-22). A wrong domain is worse than a missing address, so a search
# result is only trusted from the paid SERP route until that instance is proven sane. Set
# NF_SEARXNG_URL to opt in after checking it by hand.
#
# The paid route is capped at $1.00 a month (serp_usd). A lookup is about $0.00015, so a
# 40-candidate weekday run is roughly $0.20 a month and the rest covers the contact finder.
# The cap was $0.25 and got spent by other consumers part-way through October, which turned
# search off for everyone and left intake guessing (1 of 60 resolved, 2026-10-22).
SEARXNG_URL = os.environ.get("NF_SEARXNG_URL", "")
# SearXNG suspends an engine for minutes after a burst, so lookups are paced.
SEARXNG_MIN_INTERVAL = float(os.environ.get("NF_SEARXNG_MIN_INTERVAL", "1.5"))

# Words that are not a brand: the export's scraper leaves fragments like this in the sponsor
# column ("State", "One", "Built", "In Today's Issue"). Their obvious domains belong to
# somebody else, so even a search-confirmed host is not trusted for them, and a lead is
# imported only when a readable page names the brand.
GENERIC_NAMES = {
    "one", "state", "built", "america", "today", "todays", "issue", "news", "shop",
    "store", "home", "group", "digital", "media", "health", "life", "daily", "weekly",
    "the", "this", "that", "here", "now", "new", "best", "top", "more", "all", "local",
    "national", "global", "thebrand", "unknown", "none", "other", "misc",
    "in", "a", "an", "of", "for", "and", "on", "at", "to", "by", "with", "your", "our",
}

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


def hold_file_names(path) -> list:
    """Company names in the durable hold list, one per line, `#` comments allowed.

    A hold is different from --skip: --skip is this run, the file is every run until someone
    edits it. It exists for a candidate whose identity is genuinely unresolved (an ambiguous
    name, an unreachable site), where importing a plausible-looking domain would put a wrong
    company into the queue and every downstream stage would treat it as confirmed.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def drop_held(candidates, spec: str):
    """Candidates the operator asked to leave alone this run (--skip).

    A human call, not a rule: some resolved domains are plausible but not certain
    ("Affinity" -> affinity.studio, when the CRM company of that name is affinity.co), and
    importing one costs more to undo than to postpone. Matching is by normalised company
    name, so case, spacing and punctuation in the argument do not matter.
    """
    held = {norm(n) for n in str(spec or "").split(",") if n.strip()}
    if not held:
        return candidates, 0
    kept = [c for c in candidates if norm(c[2]) not in held]
    return kept, len(candidates) - len(kept)


def pinned_domains(spec: str) -> dict:
    """Operator-confirmed company=domain pairs, keyed by normalised company name.

    The search has the last word on a domain this pipeline has never seen, but it can rank a
    namesake first: "Affinity" comes back affinity.studio (Canva's design software) while the
    corpus sponsor is Affinity the private-capital CRM at affinity.co. A human who has read
    both pages settles it. A pin only skips the *resolution*; the ICP check still runs.
    """
    pins = {}
    for pair in str(spec or "").split(","):
        if "=" not in pair:
            continue
        name, _, domain = pair.partition("=")
        name, domain = norm(name), norm_domain(domain.strip())
        if name and domain and "." in domain:
            pins[name] = domain
    return pins


def brand_host(host: str, name: str, slug: str = "") -> bool:
    """Is this host the brand's own domain? site_match's rule, plus the dotted-name case.

    label_is_brand compares the registrable LABEL to the brand, which is right for a name
    like "Anam" (anam.ai) and wrong for a brand that carries its own TLD. Two real export
    rows fail it while their pages plainly name the brand:

      Name.com  -> name.com    label "name" vs brand "namecom"
      GroundNews -> ground.news label "ground" vs brand "groundnews"

    So a whole-host match counts too: squash the registrable domain and the brand name
    together and compare. That is an exact test, not a looser one, so it cannot turn a
    namesake into a match (a sponsor called Apollo still does not own apolloglobal.com).
    """
    if label_is_brand(host, name, slug):
        return True
    reg = registrable(host)
    want = norm(slug or name)
    return bool(reg and want) and norm(reg) == want


def exact_brand_host(host: str, name: str, slug: str = "") -> bool:
    """The host is the brand's OWN domain, not a get/try/hq variant or a carrier of the word.

    Used only as the evidence bar for accepting a domain whose page could not be fetched:
    the label has to be the brand itself (converse.com for Converse), or the whole host has
    to be the brand (name.com for Name.com). getconverse.com is a namesake until its page
    says otherwise, and that is the case this keeps out.
    """
    reg = registrable(host)
    want = norm(slug or name)
    if not reg or not want:
        return False
    return norm(reg.split(".")[0]) == want or norm(reg) == norm(name)


def name_is_specific(name: str) -> bool:
    """Is this sponsor name a brand rather than a scraped fragment?

    Used only for the weaker evidence path (a search result whose page cannot be read).
    "Converse" and "Masterclass" are brands. "State", "One", "Built" and "In Today's Issue"
    are not, and their domains belong to a government, a registrar, a lender or nobody.
    """
    words = [w for w in re.split(r"\s+", str(name or "").strip()) if w]
    if not words:
        return False
    key = norm(name)
    if key in GENERIC_NAMES:
        return False
    if len(words) == 1:
        # A one-word name needs enough letters to be a brand, unless it is an acronym.
        return len(key) >= 4 or (len(key) == 3 and str(name).isupper())
    # A multi-word name is specific unless every word is a generic one.
    return any(norm(w) not in GENERIC_NAMES for w in words)


def looks_like_domain(name: str) -> str:
    """-> the host, when the sponsor name is already a domain ("FastSpring.gg").

    The export writes a handful of sponsors that way. Guessing "fastspringgg.com" from that
    name is a wasted fetch, so the literal host is tried first.
    """
    text = str(name or "").strip().strip(".")
    if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", text.lower()):
        return text.lower()
    return ""


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


_LAST_SEARCH = 0.0


def _pace_search() -> None:
    """Keep lookups at least SEARXNG_MIN_INTERVAL apart: a burst suspends the engine."""
    global _LAST_SEARCH
    wait = SEARXNG_MIN_INTERVAL - (time.time() - _LAST_SEARCH)
    if wait > 0:
        time.sleep(wait)
    _LAST_SEARCH = time.time()


def _searx_once(query: str, engines: str) -> list:
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    req = urllib.request.Request(f"{SEARXNG_URL.rstrip('/')}/search?"
                                 f"{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except Exception:
        return []
    return payload.get("results") or []


def searx_hosts(name: str, limit: int = 8) -> list:
    """-> hosts in rank order from the local SearXNG, or [] when it cannot answer.

    Free and keyless, which is why it is asked before the paid route. A dead instance is
    not an error: the caller moves on to the SERP.
    """
    if not SEARXNG_URL:
        return []
    _pace_search()
    results = _searx_once(name, "bing")
    if not results:
        results = _searx_once(name, "")           # the aggregate, when the engine is dry
    hosts = []
    for item in results[:limit * 2]:
        host = registrable(urllib.parse.urlparse(str(item.get("url") or "")).netloc)
        if host and host not in hosts:
            hosts.append(host)
        if len(hosts) >= limit:
            break
    return hosts


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
        hosts.append(_host_of(r))
    return [h for h in dict.fromkeys(hosts) if h], cost


def _host_of(result: dict) -> str:
    """-> the registrable host of a search result, whatever shape the route answers in.

    This used to read only r["link"], and the treg SERP route answers with r["url"] and
    r["domain"]. Every paid lookup therefore came back as "no hosts", the resolver fell
    through to guessing, and the spend was recorded ($0.2638 in October, 878 lookups) while
    the results were thrown away. That is why 54 of 60 candidates came back unresolved
    (2026-10-22).
    """
    for key in ("url", "link", "domain", "displayed_url"):
        raw = str(result.get(key) or "").strip()
        if not raw:
            continue
        if "//" not in raw:
            raw = "https://" + raw
        host = registrable(urllib.parse.urlparse(raw).netloc)
        if host:
            return host
    return ""


def guesses_for(name: str, slug: str = "") -> list:
    literal = looks_like_domain(name) or looks_like_domain(slug)
    base = re.sub(r"[^a-z0-9]", "", (slug or kebab(name)).lower())
    out = [literal] if literal else []
    if base:
        out += [f"{base}.{s}" for s in DOMAIN_SUFFIXES]
        out += [f"{p}{base}.com" for p in DOMAIN_PREFIXES]
        out += [f"{base}{w}.com" for w in DOMAIN_SUFFIX_WORDS]
    seen, uniq = set(), []
    for d in out:
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def resolve_domain(name: str, slug: str, budget: dict) -> tuple:
    """-> (domain, evidence). Free search first, then the paid route, then guess-and-check.

    The guess fallback runs only when no search named a host that carries the brand's own
    name: guessing further after that is how a namesake gets imported.

    One case is new (2026-10-22). A brand whose site refuses the fetcher used to be thrown
    away even when the search had put its own domain first: converse.com, sap.com,
    adobe.com and masterclass.com all answer the SERP and then hand back zero bytes. When
    the single most-branded host is an exact whole-host match and the name is specific
    enough to be a brand, that host is accepted with the unreadable page stated as the
    evidence, so the note on the lead says exactly how it was resolved. Anything vaguer
    ("State") still needs a readable page.
    """
    branded, blocked = [], []
    hosts = searx_hosts(name)
    if not hosts and budget.get("serps_left", 0) > 0:
        budget["serps_left"] -= 1
        budget["calls"] = budget.get("calls", 0) + 1
        hosts, cost = serp_hosts(name)
        budget["cost"] = budget.get("cost", 0.0) + cost
        record_serps(cost, 1)               # spend is recorded as it happens, not at the end
    for host in hosts:
        if not brand_host(host, name, slug):
            continue
        # Only the best-ranked branded host is considered. Walking further down the results
        # is how a namesake gets picked: "Converse" ranks converse.com first (unreadable to
        # the fetcher) and converse.edu second, and a university page happily names
        # "Converse", so the second choice would have been imported as the shoe brand.
        branded.append(host)
        html = fetch(f"https://{host}/")
        if page_names_brand(html, name):
            return host, f"search result {host} names the brand"
        if not html:
            blocked.append(host)
        break
    if blocked and name_is_specific(name):
        exact = [h for h in blocked if exact_brand_host(h, name, slug)]
        if len(exact) == 1:
            return exact[0], (f"search result {exact[0]} is the brand's own domain; the page "
                              f"could not be fetched, so the name is all the evidence")
    if branded:
        return "", ""
    for domain in guesses_for(name, slug)[:5]:
        html = fetch(f"https://{domain}/", timeout=8)
        if html and brand_host(domain, name, slug) and page_names_brand(html, name):
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
    ap.add_argument("--serp-monthly-cap", type=float, default=1.00,
                    help="stop searching once this month's search spend reaches this")
    ap.add_argument("--skip", default="",
                    help="comma-separated company names to leave out of this run, e.g. "
                         "\"Affinity,Liberty Capital\" - for a domain that needs a human eye")
    ap.add_argument("--pin-domain", default="",
                    help="company=domain pairs that skip resolution, e.g. \"Affinity=affinity.co\"; "
                         "for a domain a human has confirmed and the search ranks wrongly")
    ap.add_argument("--hold-file", default=str(HOLD_FILE),
                    help="durable hold list, one company name per line; applies to every run "
                         "(pass '' to ignore it)")
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
    # Preferred tier first (MEDIUM ahead of HIGH, see scripts/_lead_rank.py), then the most
    # placements, then the name. QUALITY_ORDER is the threshold scale above and must not be used
    # here: it ranks HIGH above MEDIUM, which is the order this sort exists to invert.
    candidates, skip_count = drop_held(candidates, args.skip)
    if skip_count:
        print(f"  held back by --skip: {skip_count} candidate(s)")
    if args.hold_file:
        file_held_names = hold_file_names(args.hold_file)
        candidates, file_held = drop_held(candidates, ",".join(file_held_names))
        if file_held:
            print(f"  held back by {args.hold_file}: {file_held} candidate(s)")
    candidates.sort(key=lambda t: (quality_rank(t[1]), -t[0], t[2].lower()))
    print(f"  export: {len(rows)} row(s); {len(candidates)} qualified candidate(s) not in "
          f"the CRM at {args.min_quality}+ with {args.min_placements}+ placement(s)")

    pins = pinned_domains(args.pin_domain)
    if pins:
        print(f"  pinned by --pin-domain: {', '.join(sorted(pins))}")

    added, unresolved, failed, off_icp = [], [], [], []
    for placements, quality, name, r in candidates[: args.limit]:
        slug = slugs.get(norm(name), "")
        if norm(name) in pins:
            domain, evidence = pins[norm(name)], "domain pinned by the operator"
        else:
            domain, evidence = resolve_domain(name, slug, budget)
        if not args.apply:
            print(f"  would import {name:<28} ({quality}, {placements} placement(s))")
            print(f"      domain {domain or 'UNRESOLVED':<28} {evidence or 'no match'}")
            continue
        if not domain:
            unresolved.append(name)
            print(f"  no domain  {name} - skipped (nothing to enrich or pitch)")
            continue
        # Off-ICP sponsors do not become leads: the export's tier is an outreach score, not a
        # size or fit judgement, so an enterprise brand scores MEDIUM and would ride into the
        # first-touch queue beside the small shops (2026-10-12: Anthropic, Atlassian, AWS, Box).
        fit, why, detail = icp_fit.verdict(name, domain)
        if not fit:
            off_icp.append((name, domain, detail))
            print(f"  off-ICP    {name:<28} {domain:<28} {detail}")
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
    print(f"\nimported {len(added)} | off-ICP {len(off_icp)} | unresolved {len(unresolved)} | "
          f"failed {len(failed)} | search spend ${budget['cost']:.4f}"
          f"{' | DRY RUN - nothing written' if not args.apply else ''}")
    if off_icp:
        print(f"  off-ICP (size band {icp_fit.size_band()[0]}-{icp_fit.size_band()[1]} employees "
              f"or icp/off_icp.txt): " + ", ".join(f"{n} ({d})" for n, d, _ in off_icp[:10])
              + (" ..." if len(off_icp) > 10 else ""))
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
