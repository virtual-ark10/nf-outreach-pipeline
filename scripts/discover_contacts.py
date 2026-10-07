#!/usr/bin/env python3
"""discover_contacts.py — an address and a name for the CRM leads that lack them.

The uncontacted pool is the send cap's real ceiling, and the export cannot fill it: it
carries companies, angles and pre-written bodies, and no contact column at all. So every
address has to be found, and this script is that step, three engines cheapest first:

  1. the free site scrape — the contact/about/team pages of the lead's own domain, using
     the local-SEO pipeline's contact_finder. One shared implementation means one set of
     URL rules: an address at another company's domain or on the platform the site was
     built with is refused here exactly as it is there.
  2. treg's free people route (treg.people.search, keyed on the domain) for a
     decision-maker's name. A name is not decoration: the draft composer skips any lead it
     cannot greet, and every cheap address finder needs a name to work from.
  3. treg's paid name-first find (~$0.005 an address, billed per success) plus its
     verifier, capped per run and per month, and the address is dropped if the verifier
     calls it invalid.

Hunter is deliberately NOT called here. scripts/enrich_leads_hunter.py owns that
allowance (50 searches a month, 10 a day) and two engines spending the same pool is how a
month runs out in a week.

Only empty fields are written. Every change appends a note to the lead's timeline, so
where an address came from is auditable rather than a silent edit.

  python3 scripts/discover_contacts.py                     # dry run: who, and with what
  python3 scripts/discover_contacts.py --apply --max 20
  python3 scripts/discover_contacts.py --apply --no-find    # free engines only
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("NF_ENV_FILE", "/home/boxed/resend-pad/.env"))
CRM = os.environ.get("NF_CRM_URL", "http://127.0.0.1:3002")
USAGE_FILE = Path(os.environ.get("NF_TREG_USAGE", REPO / "data" / "treg-usage.json"))

# The scrape, the URL rules and the treg finder all live in the local-SEO pipeline and are
# imported rather than reimplemented: a second copy of "does this address belong to this
# lead" is a second thing to get wrong, and both pipelines pitch the same kind of company.
SL_REPO = Path(os.environ.get("SL_REPO", "/home/boxed/local-seo-leadgen"))
if str(SL_REPO) not in sys.path:
    sys.path.insert(0, str(SL_REPO))
try:
    import site_match                                  # noqa: E402
    import contact_finder                              # noqa: E402
    import email_finder as finder                      # noqa: E402
except ImportError as e:                               # fail closed: never scrape unruled
    print(f"cannot import the shared URL rules from {SL_REPO} ({e}) - refusing to run")
    sys.exit(1)
finder.USAGE_FILE = USAGE_FILE                         # NF's spend stays in NF's ledger


def load_token() -> str:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PAD_TOKEN=") or line.startswith("CRM_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get("CRM_TOKEN", "")


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


def month_spend() -> float:
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    return float((data.get(datetime.now().strftime("%Y-%m")) or {}).get("usd", 0.0))


def targets_from(leads, limit: int) -> list:
    """Uncontacted CRM leads that need an address, or a name to be greeted by.

    A lead that already has an address but nobody to greet it is still a lead the composer
    throws away ("no contact name, a greeting would be invented"), so it is a target too.
    """
    out = []
    for l in leads:
        if str(l.get("stage") or "") != "leads":
            continue
        if str(l.get("first_contact_at") or "").strip():
            continue
        if l.get("unsubscribed") or l.get("bounced"):
            continue
        if not str(l.get("domain") or "").strip():
            continue
        has_email = bool(str(l.get("email") or "").strip())
        has_name = bool(str(l.get("contact_name") or "").strip())
        if has_email and has_name:
            continue
        out.append(l)
    # Address-less leads first: an address is what makes a lead sendable at all.
    out.sort(key=lambda l: (bool(str(l.get("email") or "").strip()), str(l.get("company") or "")))
    return out[:limit]


def name_from(values) -> str:
    for v in values:
        v = str(v or "").strip()
        if v and re.match(r"^[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*)+$", v):
            return v
    return ""


def domain_confirms_brand(domain: str, name: str) -> tuple:
    """-> (ok, why). A wrong domain is worse than a missing address: it finds a stranger's
    mailbox and the draft then pitches the wrong company (a sponsor called Anam resolved to
    anam.com when the company is anam.ai). So the site has to name the brand before any
    engine is spent on the lead. An unfetchable site is not a confirmation either.
    """
    for path in ("", "/contact", "/about"):
        html = contact_finder.get_text(f"https://{domain}{path}")
        if not html:
            continue
        if site_match.page_names_brand(html, name):
            return True, ""
        return False, "the site does not name the brand"
    return False, "could not load the site to confirm the domain"


def main() -> int:
    ap = argparse.ArgumentParser(description="fill missing contacts in the NewsletterFIT CRM")
    ap.add_argument("--max", type=int, default=20, help="leads to work this run")
    ap.add_argument("--max-finds", type=int, default=10, help="paid address finds this run")
    ap.add_argument("--budget", type=float, default=0.05, help="hard USD ceiling for this run")
    ap.add_argument("--monthly-cap", type=float, default=0.30,
                    help="stop finding once this month's spend reaches this")
    ap.add_argument("--no-find", action="store_true", help="free engines only (scrape + names)")
    ap.add_argument("--apply", action="store_true", help="write to the CRM (default: plan only)")
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
    targets = targets_from(leads, args.max)
    spend = month_spend()
    finding = not args.no_find and spend < args.monthly_cap
    print(f"  CRM: {len(leads)} lead(s) | targets this run: {len(targets)} | "
          f"finder spend this month ${spend:.4f} of ${args.monthly_cap:.2f}"
          f"{'' if finding else ' -> paid finds disabled'}")

    stats = {"addresses_free": 0, "addresses_paid": 0, "names": 0, "no_address": 0,
             "no_name": 0, "rejected": 0, "unconfirmed": 0}
    spent = 0.0
    finds = 0
    for lead in targets:
        company = str(lead.get("company") or "")
        domain = site_match.norm_domain(lead.get("domain"))
        email = str(lead.get("email") or "").strip().lower()
        name = str(lead.get("contact_name") or "").strip()
        title = str(lead.get("contact_title") or "").strip()
        how = []
        if not args.apply:
            print(f"  {company:<30} {domain:<26} email={email or '-':<34} name={name or '-'}")
            continue

        # Nothing is spent on a domain that is not the brand's own: a namesake site yields a
        # stranger's address, and the draft then pitches the wrong company.
        ok, why = domain_confirms_brand(domain, company)
        if not ok:
            stats["unconfirmed"] += 1
            print(f"  {company:<30} {domain:<26} SKIPPED - {why}")
            continue

        # 1. the free site scrape (and it is where a name usually comes from too)
        if not email:
            found, src, _phone, person, note = contact_finder.find_email_for_domain(domain, max_pages=6)
            if found:
                email, how = found, how + [f"free scrape ({src})"]
                stats["addresses_free"] += 1
            elif note:
                print(f"    {company}: scrape found nothing that belongs to it - {note[:90]}")
            if not name and person:
                name, how = person, how + ["name off the site"]

        # 2. treg's free people route for a name (and a free address if it carries one)
        if not name:
            nm, ttl, free_email, served, cost, err = finder.find_person(domain)
            spent += cost
            if err:
                print(f"    {company}: name lookup error {err[:80]}")
            if nm:
                name, title, how = nm, ttl or title, how + [f"treg name ({served or 'people.search'})"]
                stats["names"] += 1
                if not email and free_email:
                    email, how = free_email.lower(), how + ["address from the people route"]
                    stats["addresses_free"] += 1

        # 3. treg's paid name-first find, verified before it is kept
        if finding and name and not email and finds < args.max_finds and spent < args.budget:
            addr, status, served, cost, err = finder.find_email(domain, name)
            spent += cost
            finds += 1
            if err:
                print(f"    {company}: find error {err[:80]}")
            if addr:
                vstatus, vcost, _ = finder.verify_email(addr)
                spent += vcost
                if vstatus == "invalid":
                    stats["rejected"] += 1
                    print(f"    {company}: {addr} failed verification, dropped")
                else:
                    email = addr
                    how = how + [f"treg find ({served}, {vstatus or 'unverified'})"]
                    stats["addresses_paid"] += 1

        if not email:
            stats["no_address"] += 1
        if not name:
            stats["no_name"] += 1
        if not (email or name):
            print(f"  nothing   {company:<28} {domain} (no address, no name)")
            continue

        body = {}
        if str(lead.get("email") or "").strip().lower() != email and email:
            body["contact_email"] = email
        if name and name != str(lead.get("contact_name") or "").strip():
            body["contact_name"] = name
        if title and title != str(lead.get("contact_title") or "").strip():
            body["contact_title"] = title
        if not body:
            print(f"  unchanged {company:<28} {email or '-':<34} {name or '-'}")
            continue
        code, resp = crm_call("PATCH", f"/api/leads/{urllib.parse.quote(str(lead.get('id')))}",
                              token, body)
        if code not in (200, 201):
            print(f"  FAILED    {company:<28} HTTP {code} {resp.get('error', '')}")
            continue
        crm_call("POST", f"/api/leads/{urllib.parse.quote(str(lead.get('id')))}/note", token,
                 {"body": "contact discovery: " + "; ".join(how)})
        print(f"  updated   {company:<28} {email or '-':<34} {name or '-'}  [{'; '.join(how)}]")
        time.sleep(0.2)

    if args.apply and spent:
        data = {}
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        month = data.setdefault(datetime.now().strftime("%Y-%m"), {})
        month["usd"] = round(float(month.get("usd", 0.0)) + spent, 6)
        month["finds"] = int(month.get("finds", 0)) + finds
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"\naddresses: {stats['addresses_free']} free, {stats['addresses_paid']} paid | "
          f"names: {stats['names']} | dropped by the verifier: {stats['rejected']}")
    print(f"still without an address: {stats['no_address']} | still without a name: {stats['no_name']}")
    if stats["unconfirmed"]:
        print(f"skipped, domain does not confirm as the brand: {stats['unconfirmed']} "
              f"(fix the domain, then re-run)")
    print(f"spent this run: ${spent:.4f} ({finds} paid find(s))"
          f"{'' if args.apply else ' | DRY RUN - nothing written'}")
    return 0


if __name__ == "__main__":
    import urllib.parse  # used in the PATCH path
    sys.exit(main())
