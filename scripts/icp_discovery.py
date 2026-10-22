#!/usr/bin/env python3
"""Daily ICP candidate discovery from the open web, for the weeks the KG pool is spent.

`icp_research.py` discovers companies from a metered Diffbot export. When that pool is empty it
can only reuse its cache, so the daily ICP job imports nothing new. This script is the free
supply that keeps the pipeline fed: for a rotating slice of the ICP seeds it reads the
"alternatives to <seed>" pages with TinyFish, collects the vendor domains those pages name, drops
anything already in the CRM or ruled off the list, and hands the remainder to
`import_domain_list.py`, which applies the real gates (brand on the page, 11-200 employees,
off_icp list) before a lead is written.

    python3 scripts/icp_discovery.py                 # plan: what would be candidates
    python3 scripts/icp_discovery.py --seeds 6 --apply

Cheap by construction: TinyFish search and fetch are free, and the paid work (addresses) stays
where it belongs, in the 08:00 contact pass.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SEEDS = REPO / "icp" / "seeds.txt"
OFF_ICP = REPO / "icp" / "off_icp.txt"
CANDIDATES = REPO / "data" / "domain-candidates.txt"
STATE = REPO / "data" / "icp-discovery-state.json"

# Pages that list competitors but are not companies: directory, review, news and social hosts.
SKIP = {
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com",
    "sourceforge.net", "producthunt.com", "saashub.com", "alternativeto.net", "slashdot.org",
    "reddit.com", "medium.com", "substack.com", "wikipedia.org", "youtube.com", "youtu.be",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "crunchbase.com", "pitchbook.com",
    "techcrunch.com", "forbes.com", "businessinsider.com", "gartner.com", "quora.com",
    "github.com", "news.ycombinator.com", "google.com", "g2crowd.com", "similarweb.com",
    "apollo.io", "clay.com", "zoominfo.com",
}
SOCIAL = ("linkedin.", "twitter.", "facebook.", "instagram.", "youtube.", "tiktok.", "x.com")
# Consumer platforms turn up in "alternatives" prose and are not ICP buyers. The free lane cannot
# always measure their headcount, so the importer keeps them; this list is the cheap guard.
CONSUMER = {
    "spotify.com", "netflix.com", "amazon.com", "apple.com", "microsoft.com", "meta.com",
    "tiktok.com", "uber.com", "airbnb.com", "disney.com", "nytimes.com", "bbc.co.uk",
    "walmart.com", "ebay.com", "booking.com", "expedia.com", "paypal.com", "stripe.com",
}


def norm_domain(value: str) -> str:
    d = str(value or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]
    return d.strip(".")


def registrable(host: str) -> str:
    """example.co.uk stays whole; app.example.com becomes example.com."""
    host = norm_domain(host)
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return ""
    tail = ".".join(parts[-2:])
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        tail = ".".join(parts[-3:])
    return tail


def seeds_from_crm() -> list[tuple[str, str]]:
    """No seeds file: mine alternatives to the ICP companies already in band.

    The CRM is the only seed list that is guaranteed to exist and to be on-ICP, since every row
    in it passed the same gates this script feeds.
    """
    con = sqlite3.connect(db_path())
    rows = con.execute(
        "SELECT company, domain FROM leads WHERE domain IS NOT NULL AND domain != '' "
        "AND (source = 'icp_research' OR lead_source = 'icp_research') "
        "AND (archived_at IS NULL) ORDER BY rowid DESC LIMIT 80").fetchall()
    con.close()
    out = []
    for company, domain in rows:
        d = norm_domain(domain)
        if d:
            out.append(("icp", d))
    return out


def load_seeds() -> list[tuple[str, str]]:
    if not SEEDS.exists():
        return seeds_from_crm()
    out, segment = [], "unspecified"
    for line in SEEDS.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            segment = line[1:-1].strip()
            continue
        if line.startswith("!"):          # anchor: scanned for context, never imported
            continue
        d = norm_domain(line)
        if d:
            out.append((segment, d))
    return out


def db_path() -> pathlib.Path:
    for p in (os.environ.get("OUTREACH_DB"),
              "/home/boxed/resend-pad/data/outreach.db",
              str(REPO / "pad" / "data" / "outreach.db")):
        if p and pathlib.Path(p).exists():
            return pathlib.Path(p)
    raise SystemExit("no CRM database found")


def known() -> tuple[set[str], set[str]]:
    con = sqlite3.connect(db_path())
    rows = con.execute("SELECT company, domain FROM leads").fetchall()
    con.close()
    domains = {norm_domain(r[1]) for r in rows if r[1]}
    names = {re.sub(r"[^a-z0-9]+", "", str(r[0] or "").lower()) for r in rows}
    return domains, names


def off_list() -> set[str]:
    if not OFF_ICP.exists():
        return set()
    return {norm_domain(line.split("#")[0]) for line in OFF_ICP.read_text().splitlines()
            if norm_domain(line.split("#")[0])}


def tinyfish(args: list[str], timeout: int = 120) -> dict:
    """Run the TinyFish CLI and return its JSON. Empty dict on any failure."""
    exe = os.environ.get("TINYFISH_BIN") or "tinyfish"
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    raw = p.stdout or ""
    start = raw.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(raw[start:])
    except ValueError:
        return {}


def search_urls(query: str, limit: int = 6) -> list[str]:
    d = tinyfish(["search", "query", query])
    rows = d.get("results") or d.get("data") or []
    urls = []
    for r in rows if isinstance(rows, list) else []:
        u = r.get("url") or r.get("link") if isinstance(r, dict) else None
        if u:
            urls.append(u)
    return urls[:limit]


def fetch_pages(urls: list[str]) -> list[dict]:
    if not urls:
        return []
    d = tinyfish(["fetch", "content", "get", "--format", "markdown", "--links", *urls], timeout=180)
    return [r for r in (d.get("results") or []) if isinstance(r, dict)]


def domains_in(page: dict) -> set[str]:
    blob = " ".join(str(page.get(k) or "") for k in ("text", "description", "final_url"))
    links = page.get("links") or []
    blob += " " + " ".join(l if isinstance(l, str) else str(l.get("url") or "") for l in links)
    found = set()
    for m in re.finditer(r"https?://([A-Za-z0-9._-]+)", blob):
        found.add(registrable(m.group(1)))
    for m in re.finditer(r"\b([a-z0-9][a-z0-9-]{1,40}\.(?:com|io|ai|co|app|dev|tech|net|org))\b", blob):
        found.add(registrable(m.group(1)))
    return {d for d in found if d and not any(s in d for s in SOCIAL)}


def company_from_domain(domain: str) -> str:
    label = domain.split(".")[0]
    return re.sub(r"[-_]+", " ", label).title()


def rotate(seeds: list[tuple[str, str]], take: int) -> tuple[list[tuple[str, str]], int]:
    """A slice of the seed list plus the cursor to store, so each day mines different seeds."""
    idx = 0
    if STATE.exists():
        try:
            idx = int(json.loads(STATE.read_text()).get("cursor", 0))
        except (ValueError, TypeError):
            idx = 0
    if not seeds:
        return []
    picked = [seeds[(idx + i) % len(seeds)] for i in range(min(take, len(seeds)))]
    return picked, (idx + take) % len(seeds)


def main() -> int:
    ap = argparse.ArgumentParser(description="discover ICP candidates from the open web")
    ap.add_argument("--seeds", type=int, default=6, help="seed companies to mine this run")
    ap.add_argument("--pages", type=int, default=2, help="alternatives pages per seed")
    ap.add_argument("--apply", action="store_true", help="run import_domain_list --apply")
    args = ap.parse_args()

    seeds = load_seeds()
    picked, cursor = rotate(seeds, args.seeds)
    known_domains, known_names = known()
    off = off_list()
    print(f"seeds: {len(seeds)} in list, {len(picked)} mined this run "
          f"| CRM: {len(known_domains)} domains | off list: {len(off)}")

    candidates: dict[str, str] = {}
    for segment, seed in picked:
        company = company_from_domain(seed)
        urls = [u for u in search_urls(f"{company} alternatives competitors")
                if registrable(u) not in SKIP and registrable(u) != seed]
        pages = fetch_pages(urls[:args.pages])
        found = set()
        for page in pages:
            found |= domains_in(page)
        keep = sorted(d for d in found
                      if d and d not in SKIP and d != seed
                      and not d.endswith((".gov", ".edu", ".mil", ".eu", ".int"))
                      and not d.split(".")[0] in ("blog", "news", "docs", "support", "help", "status")
                      and d not in known_domains and d not in off and d not in CONSUMER
                      and re.sub(r"[^a-z0-9]+", "", company_from_domain(d).lower()) not in known_names)
        print(f"  {seed:<28} [{segment}] pages={len(pages)} candidates={len(keep)}")
        for d in keep[:12]:
            candidates.setdefault(d, segment)

    if not candidates:
        print("  no new candidates this run")
        STATE.write_text(json.dumps({"cursor": cursor}, indent=1))
        return 0

    lines = [f"# discovered {len(candidates)} candidate(s); gates run in import_domain_list.py"]
    for d, segment in sorted(candidates.items()):
        lines.append(f"{company_from_domain(d)},{d}")
    CANDIDATES.write_text("\n".join(lines) + "\n")
    print(f"  wrote {CANDIDATES} ({len(candidates)} candidates)")
    STATE.write_text(json.dumps({"cursor": cursor}, indent=1))

    if not args.apply:
        print("  plan only: re-run with --apply to import through the ICP gates")
        return 0
    return subprocess.call([sys.executable, str(REPO / "scripts" / "import_domain_list.py"),
                            "--file", str(CANDIDATES), "--apply"], cwd=str(REPO))


if __name__ == "__main__":
    sys.exit(main())
