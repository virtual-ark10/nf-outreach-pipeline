#!/usr/bin/env python3
"""Is a lead inside the ICP? One module decides, so intake, drafting and the send gate agree.

Why this exists. The corpus intake imports whoever sponsors a newsletter in the corpus, at
MEDIUM or better, and the tier is the sponsor export's own outreach score, not a size or
fit judgement. So an enterprise brand that scores MEDIUM (Box 59, Atlassian 53, Airtable 68)
entered the first-touch queue beside the small shops, and on 2026-10-12 the day's sends read
as a list of top-tier logos: Anthropic, Atlassian, AWS, Box, Brex, MongoDB, Teleport. The
newsletter-sponsor supply is genuinely publisher-advertiser shaped and will keep producing
those names, so the fit has to be checked rather than assumed.

The rule is the ICP spec's own size band (`icp/icp_spec.json`, discovery.bands.employees,
default 11-200) plus a hand-held list in `icp/off_icp.txt` for names the free size lane
cannot measure. Size comes from treg's FREE quickenrich lane (charged $0 measured), cached
in `data/icp-size-cache.json` so a re-check costs nothing.

Three deliberate choices:
  * an unmeasurable company is KEPT (reason `size-unknown`), never dropped. A company we
    could not look up is not evidence of a bad lead, and dropping it would silently shrink
    the pool to whatever the free lane happens to index.
  * only the upper bound excludes. Below the lower bound is reported but not dropped: a
    six-person company does buy newsletter ads.
  * the exclusion list is data (`icp/off_icp.txt`), not code, so it can be edited without
    touching the pipeline.

Usage
  python3 scripts/icp_fit.py --scan              # fill the size cache for every CRM domain
  python3 scripts/icp_fit.py --report            # the size spread and what would be excluded
  python3 scripts/icp_fit.py --check box.com
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "icp", "icp_spec.json")
OFF_LIST = os.path.join(REPO, "icp", "off_icp.txt")
CACHE = os.environ.get("NF_ICP_SIZE_CACHE", os.path.join(REPO, "data", "icp-size-cache.json"))
CRM_DB = os.environ.get("NF_CRM_DB", "/home/boxed/resend-pad/data/outreach.db")
TREG = os.environ.get("TREG_BIN", "/home/boxed/.local/bin/treg")

DEFAULT_BAND = (11, 200)
# The free lane: quickenrich.people.search returns people, and the company firmographics
# (employee_count, revenue, industry) ride along with each row. Measured cost on this box: $0.
LANE = "quickenrich.people.search"


def size_band():
    """(low, high) employees from the ICP spec, defaulting to the documented 11-200."""
    try:
        spec = json.load(open(SPEC))
        band = spec.get("discovery", {}).get("bands", {}).get("employees")
        if isinstance(band, list) and len(band) == 2:
            return int(band[0]), int(band[1])
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_BAND


def band_max(band):
    """"5000 - 9999" -> 9999, "1 - 10" -> 10, "11+" -> 11, "50" -> 50, junk -> None."""
    if band is None:
        return None
    nums = [int(n) for n in re.findall(r"\d+", str(band))]
    if not nums:
        return None
    return max(nums)


def band_min(band):
    """The LOW end of a band: "100 - 249" -> 100, ">10000" -> 10000, "1 - 10" -> 1.

    The exclusion decision uses this, not the high end, because the free lane's bands are
    LinkedIn-style headcounts and they run high and coarse. Reading "100 - 249" as 249 would
    have thrown out most of the mid-market pool the ICP research scan had just imported on a
    different source's numbers (apify, clay, cognism, coresignal, lusha: all 100-249). Only a
    band whose floor is already over the ceiling is a decision; a straddling band is not.
    """
    if band is None:
        return None
    nums = [int(n) for n in re.findall(r"\d+", str(band))]
    if not nums:
        return None
    return min(nums) if len(nums) > 1 else nums[0]


def norm_domain(value):
    v = str(value or "").strip().lower()
    if "@" in v and "/" not in v.split("@")[-1]:
        v = v.split("@")[-1]                   # an email address -> its domain
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v).split("/")[0].split("?")[0]
    return v.strip(".").removeprefix("www.")


def load_cache():
    try:
        return json.load(open(CACHE))
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)
    os.replace(tmp, CACHE)


def off_list_entries():
    """Hand-held exclusions: domains and company names, one per line, # comments."""
    out = []
    try:
        for line in open(OFF_LIST):
            line = line.split("#")[0].strip().lower()
            if line:
                out.append(line)
    except OSError:
        pass
    return out


def on_off_list(company, domain, entries=None):
    entries = off_list_entries() if entries is None else entries
    dom = norm_domain(domain)
    name = re.sub(r"[^a-z0-9]+", "", str(company or "").lower())
    for entry in entries:
        e = norm_domain(entry)
        if e and dom and (dom == e or dom.endswith("." + e)):
            return entry
        flat = re.sub(r"[^a-z0-9]+", "", entry)
        if len(flat) >= 4 and name and flat == name:
            return entry
    return None


def fetch_size(domain):
    """Free lane -> {employees, band, company, industry} or None. Never raises."""
    domain = norm_domain(domain)
    if not domain or not os.path.exists(TREG):
        return None
    payload = json.dumps({"company_url": {"include": [domain]}, "per_page": 1})
    try:
        out = subprocess.run([TREG, "call", LANE, "--method", "POST", "--data", payload],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return None
    brace = out.find("{")
    if brace < 0:
        return None
    try:
        data = json.loads(out[brace:])
    except ValueError:
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0] or {}
    # The lane can answer about a namesake: keep the row only when it names this domain.
    if norm_domain(row.get("company_url") or row.get("email_domain")) != domain:
        return None
    band = row.get("employee_count")
    return {
        "band": band,
        "employees": band_min(band),
        "band_max": band_max(band),
        "company": row.get("company_name"),
        "industry": row.get("industry"),
        "revenue": row.get("revenue"),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def size_for(domain, cache=None, allow_fetch=True):
    """Cached size for a domain; fetches once when unknown. Returns dict or None."""
    dom = norm_domain(domain)
    if not dom:
        return None
    cache = load_cache() if cache is None else cache
    if dom in cache:
        return cache[dom] or None
    if not allow_fetch:
        return None
    got = fetch_size(dom)
    cache[dom] = got or {}
    return got


def employees_of(info):
    """The headcount a cached lookup speaks for: the band's LOW end, else whatever is stored.

    The band is re-read rather than trusting the number stored beside it, so a cache written
    before the low-bound rule still reads correctly instead of needing a re-scan.
    """
    if not info:
        return None
    band = info.get("band")
    lo = band_min(band) if band else None
    return lo if lo is not None else info.get("employees")


def verdict(company, domain, employees=None, cache=None, allow_fetch=True):
    """-> (fit: bool, reason: str, detail: str). fit False means do not pitch this lead.

    The hand-held list is checked first and wins over a measurement: a name on
    icp/off_icp.txt is a decision, and a decision outranks a lookup. Otherwise the size band
    decides, and an unmeasurable company is kept rather than dropped.
    """
    low, high = size_band()
    entry = on_off_list(company, domain)
    if entry:
        return False, "off-icp-list", f"listed in icp/off_icp.txt as {entry}"
    info = None
    if employees is None:
        info = size_for(domain, cache=cache, allow_fetch=allow_fetch)
        if info:
            employees = employees_of(info)
    if employees is None:
        return True, "size-unknown", "not measurable on the free lane; kept"
    band = (info or {}).get("band")
    top = (info or {}).get("band_max")
    if top is None:
        top = employees
    if employees > high:
        # The low end of the band is already over the ceiling, so the exclusion holds whatever
        # the real headcount is. A band that straddles the ceiling is not a decision.
        return False, "too-large", (f"{band or employees} employees: even the low end is over "
                                    f"the ICP ceiling of {high}")
    if top < low:
        return True, "below-band", f"{band or employees} employees is under {low}; reported, not dropped"
    return True, "in-band", f"{band or employees} employees"


def crm_domains(include_contacted=True):
    """Every domain the CRM holds, with its company name and stage."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{CRM_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        for r in con.execute("select company, domain, website, stage, lead_source, priority, "
                             "email from leads where coalesce(deleted_at,'')=''"):
            dom = norm_domain(r["domain"] or r["website"])
            if not dom and "@" in (r["email"] or ""):
                dom = norm_domain(r["email"].split("@")[-1])
            if dom and "." in dom:
                out[dom] = {"company": r["company"], "stage": r["stage"],
                            "source": r["lead_source"], "tier": r["priority"]}
        con.close()
    except sqlite3.Error:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="fetch and cache sizes for every CRM domain")
    ap.add_argument("--report", action="store_true", help="size spread and what the rule excludes")
    ap.add_argument("--check", metavar="DOMAIN")
    ap.add_argument("--limit", type=int, default=None, help="cap the fetches in this run")
    args = ap.parse_args()

    if args.check:
        fit, reason, detail = verdict(None, args.check)
        print(f"{args.check}: fit={fit} reason={reason} ({detail})")
        return 0

    domains = crm_domains()
    cache = load_cache()
    if args.scan:
        todo = [d for d in sorted(domains) if d not in cache]
        if args.limit:
            todo = todo[:args.limit]
        print(f"{len(domains)} CRM domain(s); {len(todo)} to measure (cache holds {len(cache)})")
        # The lane itself is fast but the CLI costs ~5s of startup per call, so the scan runs a
        # bounded pool in parallel rather than one domain per six seconds (310 domains would be
        # half an hour serially, about four minutes at this width).
        workers = max(1, int(os.environ.get("NF_ICP_SCAN_WORKERS", "8")))
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_size, dom): dom for dom in todo}
            for fut in as_completed(futures):
                dom = futures[fut]
                try:
                    got = fut.result()
                except Exception:
                    got = None
                cache[dom] = got or {}
                done += 1
                if done % 50 == 0:
                    save_cache(cache)
                    print(f"  {done}/{len(todo)} measured", flush=True)
        save_cache(cache)
        print(f"cache now holds {len(cache)} domain(s) -> {CACHE}")

    low, high = size_band()
    off, unknown, inband = [], [], []
    print(f"\nICP band {low}-{high} employees | measured {len(cache)} domain(s) | "
          f"the band's LOW end decides, so a straddling band is kept")
    for dom, meta in sorted(domains.items()):
        info = cache.get(dom) or None
        emp = employees_of(info)
        fit, reason, detail = verdict(meta.get("company"), dom, employees=emp, cache=cache, allow_fetch=False)
        row = (dom, meta.get("company"), (info or {}).get("band"), meta.get("source"), meta.get("tier"), reason)
        (off if reason in ("too-large", "off-icp-list") else unknown if reason == "size-unknown" else inband).append(row)
    print(f"\nICP band {low}-{high} employees | measured {len(cache)} domain(s)")
    print(f"  in band : {len(inband)}")
    print(f"  unknown : {len(unknown)}  (kept)")
    print(f"  EXCLUDED: {len(off)}")
    for dom, company, band, source, tier, reason in sorted(off, key=lambda r: str(r[0])):
        print(f"    {dom:34} {str(company)[:26]:26} {str(band or '-'):12} {str(source)[:12]:12} {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
