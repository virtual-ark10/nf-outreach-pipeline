#!/usr/bin/env python3
"""diffbot_news.py — recent, dated, sourced coverage from the Diffbot Knowledge Graph.

The external news lens the follow-up intel scan was missing. last30days' social
lanes are degraded on this host (Reddit 403, TikTok/Instagram/YouTube 402, keyless
web search only now restored via SearXNG); type:Article is a different, working
lens that returns dated, linkable coverage with a publisher and a sentiment score.

Design rules (pinned here so every caller inherits them):
  * Article headlines only. The free-text `summary`/`description` is NEVER surfaced
    (the same rule that keeps Diffbot's org summaries out of the pipeline).
  * Entity mentions use tags.label (Diffbot-linked), so a hit is "an article that
    mentions this entity", not a loose full-text guess.
  * Paced for the free tier: 5 requests/minute, so one company per call at ~13s.
  * A company with no coverage prints "no coverage" rather than a guess.

Usage
  python3 scripts/diffbot_news.py "Nvidia" --days 30 --size 8
  python3 scripts/diffbot_news.py "Acme" "Globex" --days 14
  python3 scripts/diffbot_news.py "Nvidia" --json        # machine-readable
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CREDS = pathlib.Path(os.environ.get("DIFFBOT_CREDENTIALS", pathlib.Path.home() / ".diffbot/credentials"))
DQL_URL = "https://kg.diffbot.com/kg/v3/dql"
PACE_S = float(os.environ.get("DIFFBOT_PACE_S", "13"))  # free tier: 5 req/min


def load_token() -> str:
    env = os.environ.get("DIFFBOT_API_TOKEN")
    if env:
        return env.strip()
    for line in CREDS.read_text(encoding="utf-8").splitlines():
        if line.startswith("DIFFBOT_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"no DIFFBOT_API_TOKEN in env or {CREDS}")


def _query(dql: str, size: int, token: str):
    url = DQL_URL + "?" + urllib.parse.urlencode({"token": token, "query": dql, "size": str(size)})
    req = urllib.request.Request(url, headers={"accept": "application/json", "User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read().decode()[:200]}


def build_dql(name: str, days: int) -> str:
    """type:Article DQL. A date window scopes recency, so sortBy:date is added only
    when there is no window (matches the diffbot-news skill's sort rule)."""
    q = f'type:Article tags.label:"{name}"'
    if days and days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        q += f' date>="{since}"'
    else:
        q += " sortBy:date"
    return q


def _row(entity: dict) -> dict:
    date = ((entity.get("date") or {}).get("str") or "").lstrip("d")[:10]
    return {
        "date": date,
        "title": (entity.get("title") or "").strip(),
        "publisher": (entity.get("siteName") or "").strip(),
        "sentiment": entity.get("sentiment"),
        "url": entity.get("pageUrl") or "",
        "language": entity.get("language") or "",
    }


def coverage(name: str, days: int, size: int, token: str) -> dict:
    dql = build_dql(name, days)
    d = _query(dql, size, token)
    if d.get("_http"):
        return {"company": name, "dql": dql, "error": f"HTTP {d['_http']}", "articles": []}
    arts = []
    for row in (d.get("data") or []):
        ent = row.get("entity") or {}
        r = _row(ent)
        if r["title"] or r["url"]:
            arts.append(r)
    arts.sort(key=lambda a: a["date"], reverse=True)
    return {"company": name, "dql": dql, "hits": d.get("hits"), "articles": arts}


def main() -> int:
    ap = argparse.ArgumentParser(description="Diffbot type:Article coverage for a company")
    ap.add_argument("companies", nargs="+", help="entity name(s) to look up (tags.label)")
    ap.add_argument("--days", type=int, default=30, help="recency window; 0 = newest first, no window")
    ap.add_argument("--size", type=int, default=8, help="max articles per company")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of plain text")
    args = ap.parse_args()

    token = load_token()
    out = []
    for i, name in enumerate(args.companies):
        if i:
            time.sleep(PACE_S)  # stay inside 5 requests/minute
        out.append(coverage(name, args.days, args.size, token))

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    for blk in out:
        print(f"\n== {blk['company']}  (hits={blk.get('hits')}, window={args.days}d)")
        if blk.get("error"):
            print(f"   {blk['error']}")
            continue
        if not blk["articles"]:
            print("   no coverage found in the window")
            continue
        for a in blk["articles"]:
            s = a["sentiment"]
            sent = f"{s:+.2f}" if isinstance(s, (int, float)) else "  n/a"
            print(f"   {a['date']}  {sent}  {a['publisher'][:22]:<22} {a['title'][:88]}")
            if a["url"]:
                print(f"              {a['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
