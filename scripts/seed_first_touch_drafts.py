#!/usr/bin/env python3
"""Compose first-touch drafts for not-yet-contacted leads and put them in the pad's queue.

This is the volume path. Everything it states is read from the CRM and the corpus export, and
the send gate still verifies every figure afterwards, so the generator can be fast without
being loose: it never invents a number, never quotes the discarded search rollup, and omits a
figure it cannot ground.

Ranking: the export's sponsor score and quality first (that is the corpus's own view of who is
worth talking to), then the CRM's score. Existing drafts are updated in place by the seeder,
which is idempotent by id, and leads already contacted are skipped by the CRM query.

Dry run unless --apply. Writes the batch file either way so the copy can be reviewed.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = "/home/boxed/nf-outreach-pipeline"
PAD_DB = f"{REPO}/pad/data/outreach.db"
EXPORT_CSV = "/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv"
CORPUS_ENV = "/home/boxed/.config/newsletterfit/corpus.env"
BATCH = "/home/boxed/.hermes/cache/scratch/nf_first_touch_batch.json"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--count", type=int, default=60, help="drafts to compose (gate sends up to its cap)")
ap.add_argument("--company", action="append", default=[], help="limit to these companies (testing)")
args = ap.parse_args()


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def load_env(p):
    out = {}
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CORPUS = load_env(CORPUS_ENV)
API, TOKEN = CORPUS["NEWSLETTERFIT_API"], CORPUS["API_BEARER_TOKEN"]
PAD_TOKEN = load_env("/home/boxed/resend-pad/.env")["PAD_TOKEN"]
# The drafts table wants the address the send path sends as; the pad's own env is the source.
FROM_EMAIL = (load_env("/home/boxed/nf-outreach-pipeline/pad/.env").get("FROM_EMAIL")
              or "ian@newsletterfit.com")

_cache = {}


def pub_record(name):
    """The corpus record for a publication, exact normalized name match only."""
    key = norm(name)
    if key in _cache:
        return _cache[key]
    found = None
    for q in (name, key, " ".join(str(name).split()[:2])):
        if not q:
            continue
        try:
            req = urllib.request.Request(f"{API}/search?q={urllib.parse.quote(q)}",
                                         headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode()).get("data", {})
        except Exception:
            continue
        for p in (data.get("newsletters") or []):
            if norm(p.get("name")) == key:
                found = p
                break
        if found:
            break
    _cache[key] = found
    return found


def label_of(rec):
    """The corpus's own subscriber label, without the words around it."""
    return (str(rec.get("subscribersLabel") or "")
            .replace(" subscribers", "").replace("+", "").strip())


def fallback_recs(categories, exclude, want=3):
    """Publications in the company's own categories that take sponsors.

    Used only when the recommender produced nothing. A category neighbour is a weaker claim
    than a lookalike, so the copy does not dress it up as one.
    """
    out = []
    for cat in [c for c in (categories or []) if c][:3]:
        if len(out) >= want:
            break
        try:
            req = urllib.request.Request(f"{API}/search?q={urllib.parse.quote(str(cat))}",
                                         headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode()).get("data", {})
        except Exception:
            continue
        for p in (data.get("newsletters") or []):
            name = str(p.get("name") or "").strip()
            if not name or norm(name) in exclude or p.get("acceptsSponsors") is False:
                continue
            if any(norm(name) == norm(x[0]) for x in out):
                continue
            out.append((name, label_of(p)))
            if len(out) >= want:
                break
    return out


# --- sources --------------------------------------------------------------------------------
if not os.path.exists(EXPORT_CSV):
    print(f"  no corpus export at {EXPORT_CSV}: refusing to compose blind")
    sys.exit(2)
export = {}
with open(EXPORT_CSV, newline="", encoding="utf-8", errors="replace") as fh:
    for row in csv.DictReader(fh):
        export[norm(row.get("sponsor"))] = row
print(f"  export: {len(export)} sponsors")

# A draft already in the queue with its links minted is left alone: re-seeding it would send
# empty html through the seeder's UPDATE and destroy the tokens, and the next link pass cannot
# always recreate them in a burst. Composing is the job here, not rewriting.
def queue_index():
    try:
        req = urllib.request.Request("http://127.0.0.1:3001/api/drafts",
                                     headers={"X-Pad-Token": PAD_TOKEN})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
        rows = body.get("data") if isinstance(body, dict) else body
        rows = rows if isinstance(rows, list) else []
        return {str(x.get("id")): x for x in rows if isinstance(x, dict)}
    except Exception:
        return {}


EXISTING = queue_index()
print(f"  queue already holds {len(EXISTING)} draft(s)")

con = sqlite3.connect(f"file:{PAD_DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
leads = [dict(r) for r in con.execute(
    "SELECT * FROM leads WHERE deleted_at IS NULL AND archived_at IS NULL AND stage = 'leads' "
    "AND COALESCE(email,'') <> '' AND COALESCE(unsubscribed,0) = 0 AND COALESCE(bounced,0) = 0")]
con.close()
print(f"  CRM: {len(leads)} not-contacted lead(s) with an address")


def jlist(v):
    """Publication names from either a JSON array (the CRM) or the export's joined string.

    The export writes them human-joined ("Core Memory , Sourcery, and The Generalist"), not as
    JSON, so a JSON-only reader sees nothing and silently skips every sponsor.
    """
    if not v:
        return []
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            v = json.loads(v)
        except Exception:
            pass
    items = [str(x) for x in v] if isinstance(v, list) else re.split(
        r",|\s+and\s+", re.sub(r",\s*and\s+", ", ", str(v)))
    out = []
    for it in items:
        t = re.sub(r"^\s*and\s+|\s+and\s*$", "", it.strip().strip('"')).strip(" ,;")
        if t:
            out.append(t)
    return out


ranked = []
for l in leads:
    e = export.get(norm(l.get("company"))) or {}
    try:
        score = int(float(e.get("sponsorScore") or l.get("score") or 0))
    except (TypeError, ValueError):
        score = 0
    ranked.append((score, str(e.get("outreachQuality") or "").upper(), l, e))
ranked.sort(key=lambda t: (-t[0], t[1] != "HIGH", str(t[2].get("company"))))

if args.company:
    want = {norm(c) for c in args.company}
    ranked = [t for t in ranked if norm(t[2].get("company")) in want]
ranked = ranked[: args.count]

# --- compose --------------------------------------------------------------------------------
today = datetime.date.today().isoformat()
batch, skipped = [], []
for score, quality, l, e in ranked:
    company = (l.get("company") or "").strip()
    to = (l.get("email") or "").strip()
    name = (l.get("contact_name") or "").strip()
    greeting = name.split()[0] if name else ""
    if not greeting:
        skipped.append((company, "no contact name, a greeting would be invented"))
        continue

    sponsored_list = jlist(l.get("sponsored_pubs")) or jlist(e.get("topSponsoredPublications"))
    recs_raw = jlist(l.get("recommended_pubs")) or jlist(e.get("topRecommendedPublications"))
    recs = []
    for r in recs_raw:
        if len(recs) == 3:
            break
        rec = pub_record(r)
        if not rec:
            continue
        recs.append((r, label_of(rec)))
    if not recs:
        # The recommender yields one name for most sponsors, so the copy has to work at one
        # or two as well as three. Where it yields none, fall back to publications in the
        # company's own categories that take sponsors, and say what they are rather than
        # pretending they were matched.
        recs = fallback_recs(jlist(e.get("categories")), exclude={norm(x) for x in sponsored_list})
    if not recs:
        skipped.append((company, "no publication to recommend, nothing to offer in the email"))
        continue

    sponsored = sponsored_list
    try:
        placements = int(float(e.get("placements"))) if e.get("placements") else None
    except (TypeError, ValueError):
        placements = None

    subject_pub = sponsored[0] if sponsored else recs[0][0]
    subject = f"Spotted {company} in {subject_pub}"

    opener = (f"{placements} {company} placements in the newsletters I track"
              if placements else f"{company} shows up in the newsletters I track")
    if sponsored:
        pubs = sponsored[:3]
        opener += ", across " + (", ".join(pubs[:-1]) + " and " + pubs[-1] if len(pubs) > 1 else pubs[0])
    opener += "."

    count_word = {3: "Three", 2: "Two", 1: "One"}[len(recs)]
    lead_in = (f"{count_word} list{'s' if len(recs) > 1 else ''} worth a look:"
               if recs_raw else
               f"{count_word} publication in the same lanes, if you want a look:")
    # The pad's link pass mints one tracked link per bullet that ends with [TRACKED_LINK],
    # taking the publication name from the text before the separator. Without the placeholder
    # a draft ships with no attribution at all, which the send gate refuses.
    bullets = "\n".join(
        f"- {n} (est. {lab}): [TRACKED_LINK]" if lab else f"- {n}: [TRACKED_LINK]"
        for n, lab in recs)
    text = (
        f"Hi {greeting},\n\n"
        f"{opener}\n\n"
        "I'm building NewsletterFIT: a corpus of newsletters that reads who sponsors whom and "
        "which pubs are rising, so sponsors find fits for audiences they already pay for.\n\n"
        f"{lead_in}\n\n"
        f"{bullets}\n\n"
        f"Want me to pull the reader profiles and momentum behind {'these' if len(recs) > 1 else 'it'}?\n\n"
        "[Name], Founder, NewsletterFIT"
    )
    draft_id = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    held = EXISTING.get(draft_id)
    held_html = str((held or {}).get("html") or "")
    held_text = str((held or {}).get("text") or "")
    if held and "lt=" in held_html and "[TRACKED_LINK]" not in (held_html + held_text):
        skipped.append((company, "already queued with its links minted, left as it is"))
        continue
    batch.append({"id": draft_id,
                  "lead_id": l.get("id"), "company": company, "to": to, "subject": subject,
                  "text": text, "html": "", "from": FROM_EMAIL, "campaign": "outbound"})

with open(BATCH, "w") as fh:
    json.dump(batch, fh, indent=1)
print(f"  composed {len(batch)} draft(s) -> {BATCH}   (skipped {len(skipped)})")
for co, why in skipped[:8]:
    print(f"    skip {co[:24]:<24} {why}")
print("\n  sample:\n")
if batch:
    s = batch[0]
    print(f"    id: {s['id']}\n    to: {s['to']}\n    subject: {s['subject']}\n")
    for line in s["text"].splitlines():
        print(f"      {line}")

if not args.apply:
    print("\n  (dry run: batch written, nothing seeded. Re-run with --apply to seed and mint links.)")
    sys.exit(0)

sub = subprocess.run(["node", f"{REPO}/pad/tools/seed-drafts.cjs", "--file", BATCH],
                     cwd=REPO, capture_output=True, text=True)
print("\n  seed:", (sub.stdout or sub.stderr).strip()[-400:])

req = urllib.request.Request("http://127.0.0.1:3001/api/drafts/prepare-links", data=b"{}",
                             headers={"X-Pad-Token": PAD_TOKEN, "Content-Type": "application/json"},
                             method="POST")
with urllib.request.urlopen(req, timeout=300) as r:
    print("  prepare-links:", r.read().decode()[:300])
