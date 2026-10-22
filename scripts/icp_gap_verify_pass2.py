#!/usr/bin/env python3
"""Second pass over the quotes icp_gap_verify.py could not confirm, via a rendered fetch.

The first pass uses plain HTTP, which several vendors block for bots (403) or render
client-side (empty text). This pass asks Firecrawl (keyless) for the rendered markdown, so a
quote can still be checked against what a reader sees, and merges the outcome back into
<workdir>/verify-report.json:

    pass: exact | firecrawl | still_unverified

Workdir: $ICP_GAP_DIR, else <repo>/data/icp-gap. Run icp_gap_verify.py first; this reads the
report it wrote.
"""
import json
import os
import re
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.environ.get("ICP_GAP_DIR") or os.path.join(REPO, "data", "icp-gap")
PAGES = os.path.join(HERE, "pages")
FC = "https://api.firecrawl.dev/v2/scrape"


def strip_markdown(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm(text):
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def firecrawl(url):
    import json as _json
    body = _json.dumps({"url": url, "formats": ["markdown"], "onlyMainContent": False}).encode()
    req = urllib.request.Request(FC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as res:
        payload = _json.loads(res.read().decode("utf-8", "replace"))
    if not payload.get("success"):
        raise RuntimeError(str(payload)[:200])
    md = (payload.get("data") or {}).get("markdown") or ""
    if not md:
        raise RuntimeError("empty markdown")
    return strip_markdown(md)


def main():
    path = os.path.join(HERE, "verify-report.json")
    if not os.path.exists(path):
        print(f"no report at {path}; run scripts/icp_gap_verify.py first")
        return 2
    report = json.load(open(path, encoding="utf-8"))
    os.makedirs(PAGES, exist_ok=True)
    retried = fixed = 0
    for name, acct in report.items():
        for row in acct["rows"]:
            if row["ok"]:
                row["pass"] = "exact"
                continue
            retried += 1
            url, quote = row["url"], row["quote"]
            try:
                text = firecrawl(url)
            except Exception as exc:  # noqa: BLE001
                row["pass"] = "still_unverified"
                row["reason"] = f"firecrawl failed: {exc}"[:160]
                print(f"FAIL {name:18s} {url[:70]} -> {exc}"[:150])
                time.sleep(1)
                continue
            cache = os.path.join(PAGES, "fc_" + re.sub(r"[^a-zA-Z0-9]+", "_", url)[:100] + ".txt")
            open(cache, "w", encoding="utf-8").write(text)
            if norm(quote) in norm(text):
                row["ok"] = True
                row["pass"] = "firecrawl"
                row["reason"] = "found in rendered markdown"
                fixed += 1
                print(f"OK   {name:18s} {url[:70]}")
            else:
                words = norm(quote).split()
                found = 0
                for n in range(len(words), 2, -1):
                    if " ".join(words[:n]) in norm(text):
                        found = n
                        break
                row["pass"] = "still_unverified"
                row["reason"] = f"quote not on rendered page (leading match {found}/{len(words)} words)"
                print(f"MISS {name:18s} {url[:70]} {row['reason']}")
            time.sleep(1)
    for name, acct in report.items():
        acct["verified"] = sum(1 for r in acct["rows"] if r["ok"])
    json.dump(report, open(path, "w", encoding="utf-8"), indent=1)
    total = sum(a["checked"] for a in report.values())
    good = sum(a["verified"] for a in report.values())
    print(f"\nretried {retried}, recovered {fixed}. now {good}/{total} verified")
    for name, a in report.items():
        if a["verified"] < a["checked"]:
            print(f"  {name:18s} {a['verified']}/{a['checked']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
