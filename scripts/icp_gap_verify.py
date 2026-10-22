#!/usr/bin/env python3
"""Verify gap-research batches: does each claimed quote actually appear on the page it cites?

    python3 scripts/icp_gap_verify.py [batch1.json batch2.json ...]

Rescued from the 2026-10-21 ICP ladder research, where it was the step that kept a paraphrase
out of a prospect's inbox. For every account in every batch it fetches each evidence URL once
(cached under <workdir>/pages), strips the HTML to text, normalises whitespace and case, and
checks the quote as a substring. Results go to <workdir>/verify-report.json plus a one-line
summary per account.

Nothing here is a judgement about the account. It only answers "was this sentence on that page",
so nothing unverified can reach the report or an email. A near miss is recorded as a miss along
with the longest leading match, so a paraphrase is visible rather than silently accepted.

Workdir: $ICP_GAP_DIR, else <repo>/data/icp-gap. Default batch glob when given no files is
batch\\d+\\.json inside it. Quotes that fail here because the vendor blocks plain HTTP can be
retried through a rendered fetch with scripts/icp_gap_verify_pass2.py.
"""
import html
import json
import os
import re
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.environ.get("ICP_GAP_DIR") or os.path.join(REPO, "data", "icp-gap")
PAGES = os.path.join(HERE, "pages")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"


def slug(url):
    return re.sub(r"[^a-zA-Z0-9]+", "_", url)[:120]


def fetch(url):
    os.makedirs(PAGES, exist_ok=True)
    path = os.path.join(PAGES, slug(url) + ".txt")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return open(path, encoding="utf-8", errors="replace").read()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - the point is to record the failure
        open(path, "w", encoding="utf-8").write(f"__FETCH_FAILED__ {exc}")
        return f"__FETCH_FAILED__ {exc}"
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    open(path, "w", encoding="utf-8").write(text)
    return text


def norm(text):
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def check(url, quote):
    page = fetch(url)
    if page.startswith("__FETCH_FAILED__"):
        return {"url": url, "quote": quote, "ok": False, "reason": page[:160]}
    if norm(quote) in norm(page):
        return {"url": url, "quote": quote, "ok": True, "reason": "exact"}
    # A near miss is still a miss for the report; record how much of the quote was found so a
    # human can see whether it was a paraphrase or a different page.
    words = norm(quote).split()
    found = 0
    for n in range(len(words), 2, -1):
        if " ".join(words[:n]) in norm(page):
            found = n
            break
    return {
        "url": url,
        "quote": quote,
        "ok": False,
        "reason": f"not found (longest leading match {found}/{len(words)} words)",
    }


def main(paths):
    report = {}
    for path in paths:
        data = json.load(open(path, encoding="utf-8"))
        for acct in data:
            name = acct.get("company", os.path.basename(path))
            rows, urls = [], set()
            for entry in acct.get("evidence") or []:
                u = entry.get("url")
                q = entry.get("quote")
                if not u or not q:
                    rows.append({"url": u, "quote": q, "ok": False, "reason": "missing url or quote"})
                    continue
                if u in urls:
                    continue
                urls.add(u)
                rows.append(check(u, q))
            for entry in acct.get("api_or_integration_evidence") or []:
                u, q = entry.get("url"), entry.get("quote")
                if u and q and u not in urls:
                    urls.add(u)
                    rows.append(check(u, q))
            ok = sum(1 for r in rows if r["ok"])
            report[name] = {"checked": len(rows), "verified": ok, "rows": rows}
            print(f"{name:22s} {ok}/{len(rows)} verified")
            for r in rows:
                if not r["ok"]:
                    print(f"    MISS  {r['url'][:90]}  {r['reason']}")
    os.makedirs(HERE, exist_ok=True)
    out = os.path.join(HERE, "verify-report.json")
    json.dump(report, open(out, "w", encoding="utf-8"), indent=1)
    total = sum(v["checked"] for v in report.values())
    good = sum(v["verified"] for v in report.values())
    print(f"\n{good}/{total} claims verified across {len(report)} accounts -> {out}")
    return 0 if good == total else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = sorted(
            os.path.join(HERE, f)
            for f in os.listdir(HERE)
            if re.fullmatch(r"batch\d+\.json", f)
        ) if os.path.isdir(HERE) else []
    if not args:
        print(f"no batch files; put batch*.json in {HERE} or name them on the command line")
        sys.exit(2)
    sys.exit(main(args))
