#!/usr/bin/env python3
"""The subject line for a first touch: "Spotted <Company> in <Publication>".

The publication is the NEWEST placement in the corpus for that sponsor — the thing the
recipient will recognise as their own recent activity — resolved from
`/directory/sponsors/<slug>` (newest `placements[].publishedAt` → `placement.publication.name`).
Nothing is guessed: the placement's own evidence string back to the sponsor copy is printed
so the claim can be checked before it goes in an email.

Usage
    python3 first_email_subject.py "Brex"                  # one company
    python3 first_email_subject.py --all-first-emails      # every draft still labelled "First Email"
    python3 first_email_subject.py --all-first-emails --apply   # ...and write them to the pad

Environment: reads NEWSLETTERFIT_API + API_BEARER_TOKEN from
~/.config/newsletterfit/corpus.env and PAD_TOKEN from the pad's .env.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CORPUS_ENV = "/home/boxed/.config/newsletterfit/corpus.env"
PAD_ENV = "/home/boxed/resend-pad/.env"
PAD_BASE = "http://127.0.0.1:3001"
STAGE_LABELS = {"first email", "first_email"}


def load_env(path: str) -> dict:
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


CORPUS = load_env(CORPUS_ENV)
API = CORPUS["NEWSLETTERFIT_API"].rstrip("/")
CORPUS_HEADERS = {"Authorization": f"Bearer {CORPUS['API_BEARER_TOKEN']}"}
PAD_HEADERS = {"X-Pad-Token": load_env(PAD_ENV)["PAD_TOKEN"], "Content-Type": "application/json"}


def api_get(path: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=CORPUS_HEADERS), timeout=40) as r:
        return json.loads(r.read().decode())


def pad(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PAD_BASE + path, data=data, headers=PAD_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


def sponsor(company: str) -> tuple[str | None, dict | None]:
    """The sponsor record for a company, via the directory slug then entity search."""
    guess = company.lower().replace(" ", "-")
    try:
        data = api_get(f"/directory/sponsors/{guess}").get("data") or {}
        if data.get("placements"):
            return guess, data
    except urllib.error.HTTPError:
        pass
    try:
        found = api_get(f"/search?q={urllib.parse.quote(company)}&limit=5").get("data") or {}
        for candidate in found.get("sponsors") or []:
            if str(candidate.get("name", "")).lower() == company.lower() and candidate.get("slug"):
                slug = candidate["slug"]
                data = api_get(f"/directory/sponsors/{slug}").get("data") or {}
                if data.get("placements"):
                    return slug, data
    except Exception:  # noqa: BLE001 — a lookup miss must not break a batch run
        pass
    return None, None


def newest_placement(data: dict) -> dict | None:
    placements = [p for p in (data.get("placements") or []) if p.get("publishedAt")]
    return max(placements, key=lambda p: str(p["publishedAt"])) if placements else None


def subject_for(company: str) -> tuple[str | None, dict]:
    """("<subject>", detail) — subject is None when the corpus cannot ground one."""
    slug, data = sponsor(company)
    if not data:
        return None, {"reason": "no corpus sponsor found", "company": company}
    newest = newest_placement(data)
    if not newest:
        return None, {"reason": "sponsor has no placements", "company": company}
    name = (data.get("name") or company).strip()
    pub = ((newest.get("publication") or {}).get("name") or "?").strip()
    return f"Spotted {name} in {pub}", {
        "company": name, "slug": slug, "publications": data.get("publications"),
        "placements": len(data.get("placements") or []),
        "newest_at": str(newest.get("publishedAt"))[:10], "publication": pub,
        "evidence": str(newest.get("evidence"))[:160],
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    if "--all-first-emails" in sys.argv:
        _, payload = pad("GET", "/api/drafts")
        drafts = payload.get("data") or payload
        targets = [d for d in drafts if str(d.get("subject", "")).strip().lower() in STAGE_LABELS]
        print(f"{len(targets)} draft(s) still labelled with a stage name\n")
        for draft in targets:
            subject, detail = subject_for(draft.get("company") or "")
            print(f"  {draft['id']}\n    {json.dumps(detail)}")
            if subject is None:
                print("    -> left alone")
            else:
                print(f"    -> {subject!r}")
                if apply_:
                    status, body = pad("PUT", f"/api/drafts/{urllib.parse.quote(draft['id'])}", {"subject": subject})
                    print(f"    write: HTTP {status} {str(body)[:80]}")
            print()
        return 0
    if not args:
        print(__doc__)
        return 2
    subject, detail = subject_for(args[0])
    print(json.dumps(detail, indent=2))
    print("\n" + (subject or "no grounded subject for this company"))
    return 0 if subject else 1


if __name__ == "__main__":
    raise SystemExit(main())
