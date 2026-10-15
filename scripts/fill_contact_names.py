#!/usr/bin/env python3
"""fill_contact_names.py — a NAME for the uncontacted leads that have none, free.

Why this exists, separately from discover_contacts.py: that script asks treg's routed
`treg.people.search` for a decision maker, and the local-SEO module it borrows sends a
business-owner title filter with the request — owner / president / general manager / office
manager. NewsletterFIT's buyer is the GTM, growth or marketing person, so the filter excludes
almost everyone worth emailing, the route reports no people, and the lead stays unnamed even
though the provider holds a dozen names for that domain.

The other half of the problem is the route itself: `treg.people.search` waterfalls across
children, and its paid children ($0.0004-$0.05 a call) bill their MISSES. The routed call the
discovery job makes is therefore capped at $0.01 and rationed to a $0.30 month, so only a
handful of domains are attempted a day. Pinning the two FREE children directly costs nothing
whether they hit or miss, so this pass can cover the whole backlog in one run.

Engines, both free, called by their provider-native payloads:
  - quickenrich.people.search   {"company_url": {"include": ["domain.com"]}, "per_page": 25}
  - dropleads.people.search     {"filters": {"companyDomains": ["domain.com"]}, "pagination": {...}}

Neither returns contact data (no addresses, no phones). This pass fills names, titles and
roles; addresses stay the paid finder's job in discover_contacts.py.

Ran against the 101 uncontacted MEDIUM corpus leads that had no contact name (2026-10-14):
62 had at least one usable person, 12 of them a GTM/growth person, at $0.
apollo.people.search, despite being free, is deliberately not called: its free search is
documented as returning no contact data, returns 0 rows for these domains, and its obfuscated
last names would poison the greeting the draft composer writes.

  python3 scripts/fill_contact_names.py                      # dry run: who, and with what
  python3 scripts/fill_contact_names.py --apply
  python3 scripts/fill_contact_names.py --apply --limit 20 --source corpus
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("NF_ENV_FILE", "/home/boxed/resend-pad/.env"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")
TREG = Path(os.environ.get("NF_TREG_BIN", "/home/boxed/.local/bin/treg"))

# Free-lane responses, cached per (lane, domain): the two providers are fast but the calls are
# per-domain subprocesses, and a whole-backlog pass must survive a timeout and re-run cheaply.
CACHE_DIR = Path(os.environ.get("NF_NAME_CACHE", REPO / "data" / "name-cache"))
CACHE_TTL = 14 * 24 * 3600

# Lead quality preference lives in one place (scripts/_lead_rank.py): MEDIUM first.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lead_rank import quality_rank, tier_of        # noqa: E402

# The buyer, best first. Mirrors the pad's own contact_role vocabulary so the CRM stays
# consistent (lead rows in the wild carry: other, growth, marketing, founder, cro, gtm,
# partnerships, sales).
ROLE_ORDER = ("gtm", "growth", "partnerships", "marketing", "cro", "sales", "founder", "other")

LANES = (
    ("quickenrich.people.search",
     lambda d: {"company_url": {"include": [d]}, "per_page": 25}),
    ("dropleads.people.search",
     lambda d: {"filters": {"companyDomains": [d]}, "pagination": {"page": 1, "limit": 25}}),
)

# A generic mailbox is not a person: the composer greets a human and the finder works from a
# name. Only person-shaped addresses are allowed to fill an empty email column. The list lives
# in one place now (scripts/_address_rules.py) so the gate, the discovery pass and this pass
# cannot disagree about what a queue address is.
from _address_rules import is_role_address              # noqa: E402  (path set just above)

STOPWORDS = {"the", "inc", "llc", "ltd", "limited", "group", "co", "corp", "corporation", "ai",
             "io", "com", "hq", "app", "labs", "lab", "technologies", "technology", "company"}


def load_token() -> str:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAD_TOKEN=") or line.startswith("CRM_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get("CRM_TOKEN", "")


def crm_call(method: str, path: str, token: str, body=None, tries: int = 5):
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
                payload = json.loads(e.read().decode() or "{}")
            except Exception:
                payload = {}
            # The CRM throttles at 120 requests a minute per IP. A bulk pass must wait it out
            # rather than report the rest of the backlog as failed.
            if e.code == 429 and i < tries - 1:
                wait = 20 + 10 * i
                print(f"    429 from the CRM, waiting {wait}s")
                time.sleep(wait)
                continue
            return e.code, payload
        except Exception as e:
            if i == tries - 1:
                return -1, {"error": str(e)}
            time.sleep(1 + i)
    return 429, {"error": "rate limited out"}


def tokens(text: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in STOPWORDS}


def host_of(value: str) -> str:
    v = re.sub(r"^https?://", "", str(value or "").strip().lower()).split("/")[0]
    return v if "." in v else ""


def brand_match(company: str, domain: str, row_company: str, row_domain: str = "") -> bool:
    """Keep a person only when the employer the provider names is this lead's brand.

    A provider will happily answer with a namesake's staff (the corpus holds Stata, Delta, Zone,
    IEEE, F2 and Profound in it), and a draft built on the wrong company's employee pitches a
    stranger: the Profound lead was once matched to a Dutch monitoring-equipment maker. When the
    provider names a host, that host has to BE the lead's domain (a subdomain of it is fine);
    when it names only a company, fall back to brand-token overlap.
    """
    label = re.sub(r"^www\.", "", str(domain or "").lower()).split(".")[0]
    lead_host = re.sub(r"^www\.", "", str(domain or "").lower())
    host = host_of(row_domain)
    if host:
        # Same word, different company: profound.tech is not profound.nl (a Dutch monitoring
        # equipment maker the Profound lead was once matched to), and stata.com is not
        # statistics.com. Only the lead's own host proves it.
        return bool(lead_host) and (host == lead_host or host.endswith("." + lead_host))
    want = tokens(company) | {label} - {""}
    got = tokens(row_company)
    if not got or not want:
        return True                      # provider named no employer: nothing to contradict
    return bool(want & got)


def role_of(title: str) -> str:
    t = " " + re.sub(r"[^a-z ]", " ", (title or "").lower()) + " "
    if "gtm" in t or "go to market" in t or "go-to-market" in t:
        return "gtm"
    if "growth" in t:
        return "growth"
    if "partnership" in t or "business development" in t or "alliance" in t:
        return "partnerships"
    if "marketing" in t or "brand" in t or "content" in t or "seo" in t:
        return "marketing"
    if "revenue" in t or " cro " in t or "chief revenue" in t or "sales" in t:
        return "cro"
    if "founder" in t or "chief executive" in t or " ceo " in t or "owner" in t or " coo " in t:
        return "founder"
    return "other"


def treg_calls(lane: str, payload: dict, timeout: int = 60):
    """Call one provider directly. Free children only: nothing here can bill a miss.

    Responses are cached per (lane, domain): a re-run of this pass, or a retry after a
    timeout, must not re-ask the provider for an answer it already gave.
    """
    domain = ""
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], str):
            domain = v[0]
            break
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list) and vv:
                    domain = vv[0]
    key = re.sub(r"[^a-z0-9.-]", "_", f"{lane}__{domain}".lower())
    cache = CACHE_DIR / f"{key}.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
            return json.loads(cache.read_text(encoding="utf-8")), None
    except Exception:
        pass
    try:
        p = subprocess.run([str(TREG), "call", lane, "--method", "POST",
                            "--data", json.dumps(payload)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    body = "\n".join(l for l in (p.stdout or "").splitlines()
                     if not l.startswith("treg:") and not l.startswith("charged"))
    if "charged $" in (p.stdout or "") and "charged $0 " not in (p.stdout or ""):
        return None, "REFUSED: a call reported a charge"
    try:
        data = json.loads(body)
    except Exception:
        return None, (body or p.stderr or "")[:120]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return data, None


def people_of(data) -> list:
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    if isinstance(rows, dict):
        rows = rows.get("leads") or rows.get("people") or rows.get("results") or []
    if rows is None:
        rows = (data.get("output") or {}).get("people") or []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        name = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x).strip() \
            or str(r.get("fullName") or r.get("name") or "").strip()
        title = str(r.get("title") or r.get("job_title") or "").strip()
        email = str(r.get("email") or "").strip().lower()
        if email in ("n/a", "na", "null", "none"):
            email = ""
        out.append({
            "name": name,
            "title": title,
            "role": role_of(title),
            "company": str(r.get("company_name") or r.get("companyName") or "").strip(),
            "domain": str(r.get("company_url") or "").strip(),
            "email": email if "@" in email and not is_role_address(email) else "",
        })
    return out


def title_case(name: str) -> str:
    """'hai liu' -> 'Hai Liu'; leave 'McDonald' and 'Van Hyfte' alone."""
    if not (name.islower() or name.isupper()):
        return name
    return " ".join(w if any(c.isupper() for c in w[1:]) else w.capitalize()
                    for w in name.split())


# A name is a greeting: two real words, each at least two letters, and never a job title. The
# lanes return rows like "Dubzee W", "Senior Engineering" and "Mann Our"; a draft that greets a
# prospect with one of those tells them nobody looked at their company.
NAME_OK = re.compile(r"^[^\W\d_][^\W\d_'’. -]+(?: [^\W\d_][^\W\d_'’. -]+)+$", re.UNICODE)
TITLE_WORDS = {
    "senior", "junior", "engineering", "engineer", "manager", "management", "marketing",
    "director", "head", "lead", "leader", "sales", "team", "officer", "specialist",
    "coordinator", "executive", "intern", "developer", "founder", "president", "chief",
    "product", "growth", "data", "support", "service", "operations", "consultant", "analyst",
    "associate", "partner", "owner", "administrator", "admin", "recruiter", "student",
    "customer", "success", "account", "business", "development", "strategy", "design",
    "content", "brand", "community", "talent", "people", "human", "resources", "finance",
    "software", "solutions", "systems", "technologies", "staff", "global", "regional",
    "national", "international", "the", "and", "of", "for",
}

# An honorific is not a first name: "Ms. Ferdoushi" would greet her as "Ms.".
HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|prof|mx|sir|madam)\.?$", re.I)

# Titles that mean "not the person who buys sponsorship intelligence". A junior is a worse
# greeting than no name at all: it tells the prospect nobody looked at the company.
JUNIOR = re.compile(r"junior|jr\b|intern|graduate|student|assistant|bookkeep|develop|engineer|"
                    r"recruit|trainee|data entry|support|helpdesk", re.I)
SENIORITY = re.compile(r"chief|\bceo\b|\bcoo\b|\bcro\b|\bcmo\b|founder|owner|president|vice "
                       r"president|\bvp\b|head of|director|manager|partner|lead\b", re.I)


def usable(p: dict) -> bool:
    name = title_case(str(p.get("name") or "").strip())
    p["name"] = name
    if len(name) > 60 or not NAME_OK.match(name):
        return False
    if any(x in name.lower() for x in ("n/a", "unknown", "private", "linkedin member")):
        return False
    return not {w.lower() for w in name.split()} & TITLE_WORDS


def greeting_safe(name: str):
    """-> (ok, why not). Is this stored contact a name a draft could greet without embarrassment?

    Deliberately laxer than usable(): a provider's abbreviated "Eric C" or a bare "Lindsay" is a
    real person and a workable greeting, while "Senior Engineering", "Content Marketing" and
    "Ms. Ferdoushi" are a job title, a company and an honorific pretending to be one.
    """
    parts = str(name or "").split()
    if not 1 <= len(parts) <= 4:
        return False, "not a name shape"
    if any(HONORIFIC.match(p) for p in parts):
        return False, "honorific, not a first name"
    low = {re.sub(r"[^a-z]", "", p.lower()) for p in parts}
    if low & TITLE_WORDS:
        return False, "a job title or a company word"
    if len(parts) == 1:
        return (len(parts[0]) >= 3, "single token")
    if len(parts[0]) == 1:
        return False, "initial instead of a first name"
    return True, ""



def worth_writing(p: dict) -> bool:
    """Only a buyer-shaped person fills the contact: the lane's top hit is not enough.

    The founder rule is GTM or growth first, and a fallback down partnerships > marketing >
    revenue/BD > founder. Anyone outside that set is kept only when the title itself carries
    seniority, so a bookkeeper or a junior developer is never used to greet a sponsor.
    """
    if JUNIOR.search(p["title"] or ""):
        return False
    return p["role"] != "other" or bool(SENIORITY.search(p["title"] or ""))


def best_for(company: str, domain: str):
    """The best person the two free lanes hold for this domain, or None."""
    candidates = []
    for lane, mk in LANES:
        data, err = treg_calls(lane, mk(domain))
        if err:
            print(f"    {company}: {lane} -> {err}")
            continue
        for p in people_of(data):
            if not usable(p) or not brand_match(company, domain, p["company"], p["domain"]):
                continue
            p["lane"] = lane
            candidates.append(p)
    if not candidates:
        return None
    buyers = [p for p in candidates if worth_writing(p)]
    if not buyers:
        return None
    buyers.sort(key=lambda p: (ROLE_ORDER.index(p["role"]), -len(p["title"] or "")))
    best = buyers[0]
    best["n_candidates"] = len(buyers)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="name the uncontacted NF leads that have none, free")
    ap.add_argument("--limit", type=int, default=0, help="leads to work (0 = all)")
    ap.add_argument("--source", default="", help="corpus | icp_research (default: both)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--pace", type=float, default=1.2,
                    help="seconds between CRM writes; the CRM allows 120 requests a minute")
    ap.add_argument("--apply", action="store_true", help="write to the CRM (default: plan only)")
    ap.add_argument("--recheck", action="store_true",
                    help="clear stored contact names that are not a person's name (title rows, "
                         "single initials, obfuscated handles) so the pass can refill them")
    args = ap.parse_args()

    token = load_token()
    if not token:
        print("no CRM token (PAD_TOKEN in the pad's .env) - aborting")
        return 1
    code, payload = crm_call("GET", "/api/leads", token)
    if code != 200:
        print(f"cannot read the CRM: HTTP {code} {payload.get('error', '')} - aborting")
        return 1
    leads = payload.get("leads") or payload.get("data") or []

    force_targets = None
    if args.recheck:
        bad = []
        for l in leads:
            name = str(l.get("contact_name") or "").strip()
            if not name or str(l.get("stage")) in ("won", "no", "archived"):
                continue
            ok, why = greeting_safe(name)
            if not ok:
                bad.append((l, why))
        if args.limit:
            bad = bad[:args.limit]
        print(f"  stored contacts a draft could not greet: {len(bad)}")
        for l, why in bad:
            print(f"  not a name  {str(l.get('company'))[:26]:<26} {str(l.get('domain'))[:24]:<24} "
                  f"name={l.get('contact_name')!r} title={str(l.get('contact_title'))[:28]!r} "
                  f"-> {why}")
        if not args.apply:
            print(f"\nwould clear and refill: {len(bad)} lead(s). Re-run with --apply.")
            return 0
        for l, why in bad:
            crm_call("PATCH", f"/api/leads/{urllib.parse.quote(str(l.get('id')))}", token,
                     {"contact_name": "", "contact_title": "", "contact_role": "other"})
            crm_call("POST", f"/api/leads/{urllib.parse.quote(str(l.get('id')))}/note", token,
                     {"body": f"contact name cleared: {l.get('contact_name')!r} is not a name a "
                              f"draft could greet ({why}); refilling from the free lanes"})
            time.sleep(args.pace)
        force_targets = [l for l, _ in bad]
        print(f"  cleared {len(force_targets)}, refilling from the free lanes\n")

    if force_targets is not None:
        targets = force_targets
    else:
        targets = [l for l in leads
                   if str(l.get("stage") or "") == "leads"
                   and not str(l.get("contact_name") or "").strip()
                   and str(l.get("domain") or "").strip()
                   and not l.get("deleted_at") and not l.get("archived_at")
                   and (not args.source or str(l.get("lead_source")) == args.source)]
        targets.sort(key=lambda l: (quality_rank(l), tier_of(l) != "MEDIUM",
                                    -float(l.get("score") or 0)))
        if args.limit:
            targets = targets[:args.limit]
    print(f"  CRM: {len(leads)} lead(s) | nameless uncontacted with a domain: {len(targets)} "
          f"| tier order: MEDIUM first | engines: free lanes only")

    def work(lead):
        company = str(lead.get("company") or "").strip()
        domain = str(lead.get("domain") or "").strip().lower()
        return lead, best_for(company, domain)

    filled = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for lead, best in ex.map(work, targets):
            company = str(lead.get("company") or "").strip()
            domain = str(lead.get("domain") or "").strip().lower()
            if not best:
                print(f"  nothing   {company:<30} {domain:<26} no free-lane person")
                continue
            body = {"contact_name": best["name"],
                    "contact_title": best["title"] or None,
                    "contact_role": best["role"]}
            src = f"{best['lane']} (free)"
            # A stored shared mailbox is not an address either: a person's own address from the
            # lanes is strictly better, so it replaces support@/info@ rather than deferring to it.
            stored_email = str(lead.get("email") or "").strip()
            if best["email"] and (not stored_email or is_role_address(stored_email)):
                body["email"] = best["email"]
                src += (f"; address {best['email']}"
                        + (f" replacing the shared mailbox {stored_email}" if stored_email else ""))
            if not args.apply:
                print(f"  plan      {company:<30} {domain:<26} {best['name']:<24} "
                      f"{best['role']:<13} {best['title'][:34]:<34} [{best['lane']}] "
                      f"of {best['n_candidates']}")
                continue
            code, resp = crm_call("PATCH", f"/api/leads/{urllib.parse.quote(str(lead.get('id')))}",
                                  token, body)
            if code not in (200, 201):
                print(f"  FAILED    {company:<30} HTTP {code} {resp.get('error', '')}")
                continue
            crm_call("POST", f"/api/leads/{urllib.parse.quote(str(lead.get('id')))}/note", token,
                     {"body": "contact name: " + src})
            filled += 1
            print(f"  named     {company:<30} {domain:<26} {best['name']:<24} "
                  f"{best['role']:<13} [{best['lane']}]")
            time.sleep(args.pace)
    if args.apply:
        print(f"\nnames written: {filled} of {len(targets)} | spend $0.00")
    else:
        print(f"\nplan only. {len(targets)} lead(s) would be attempted. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
