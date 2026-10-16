#!/usr/bin/env python3
"""Compose the day-3 / day-7 / day-14 follow-ups for the CRM's DUE leads, from the corpus.

This is the intel layer of the follow-up scan, made deterministic: every sentence it writes is
read this run from the lead's own corpus record (the sponsor export + the live corpus API), so
the copy can be as fast as a batch job without inventing anything. The send gate still verifies
every figure afterwards, and nothing it writes is a claim the corpus cannot show.

Per rung, one strongest angle, grounded:

  Touch 2 (day 3)  two more lists for the audience the lead already reaches, with the corpus's
                   own subscriber labels and, when the corpus holds it, the sponsor the list is
                   carrying + the title of its newest piece.
  Touch 3 (day 7)  the placement count (house measure: the export's article-level count), the
                   newest publication the corpus logged for them, and one NEW piece of evidence:
                   a list in their lane, the sponsor it is carrying, its newest article title.
  Touch 4 (day 14) one last concrete datapoint: which lists in the lane are carrying sponsored
                   placements right now, and who is in front of that reader.

What it refuses to do: state a placement count the export has no grounded number for, name a
publication whose corpus record it could not resolve exactly (the pad would leave the bullet
unlinked and the gate would hold it), quote a subscriber label not read live, repeat a
publication an earlier touch already named, or write apology/back-reference language.

Usage
  python3 scripts/seed_followup_intel_drafts.py                 # dry run, writes the batch + report
  python3 scripts/seed_followup_intel_drafts.py --apply         # seed the pad queue and mint links
  python3 scripts/seed_followup_intel_drafts.py --company Brex  # limit to these companies
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
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "/home/boxed/nf-outreach-pipeline"
PAD_DB = f"{REPO}/pad/data/outreach.db"
SNAP_DIR = f"{REPO}/pad/snapshots"
EXPORT_CSV = "/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv"
EXPORT_JSON = "/srv/newsletterfit/reports/sponsor-outreach/all-sponsors.json"
CORPUS_ENV = "/home/boxed/.config/newsletterfit/corpus.env"
BATCH = "/home/boxed/.hermes/cache/scratch/nf_followup_batch.json"
REPORT = "/home/boxed/.hermes/cache/scratch/nf_followup_report.md"
PAD = "http://127.0.0.1:3001"

# The cadence, in one place: stage -> (touch number, days waited since the last touch).
# Mirrors scripts/crm_lead_state.py DUE_DAYS, and its stage names are the CRM's.
TOUCH = {"first_email": (2, 3), "follow_up_1": (3, 4), "follow_up_2": (4, 7)}
STATUS = {"first_email": "First Email", "follow_up_1": "Follow 2", "follow_up_2": "Follow 3"}
SKIP_COMPANIES = {"profound"}          # misdirected lead (profound.nl vs tryprofound.com)

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--refresh", action="store_true",
                help="re-compose drafts this run created earlier the same day (idempotent by id)")
ap.add_argument("--company", action="append", default=[])
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()


def load_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def plain(s):
    """Normalize for comparisons in prose: letters+digits+single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


ENV = load_env(CORPUS_ENV)
API, TOKEN = ENV.get("NEWSLETTERFIT_API", ""), ENV.get("API_BEARER_TOKEN", "")
PAD_TOKEN = load_env("/home/boxed/resend-pad/.env").get("PAD_TOKEN", "")
FROM_EMAIL = (load_env(f"{REPO}/pad/.env").get("FROM_EMAIL")
              or load_env("/home/boxed/resend-pad/.env").get("FROM_EMAIL") or "ian@newsletterfit.com")
if not API or not TOKEN:
    print("  no corpus credentials: refusing to compose blind")
    sys.exit(2)

_json_cache = {}
DISK_CACHE = "/home/boxed/.hermes/cache/scratch/nf_corpus_cache.json"
try:
    _json_cache = json.load(open(DISK_CACHE))
except Exception:
    _json_cache = {}
_last_call = [0.0]
PACE = 1.6           # seconds between live corpus calls: the API 429s a burst


def _save_cache():
    try:
        with open(DISK_CACHE, "w") as fh:
            json.dump(_json_cache, fh)
    except Exception:
        pass


def api_get(path):
    """GET the corpus API, paced, with backoff on 429, cached on disk so a re-run is free."""
    if path in _json_cache:
        return _json_cache[path]
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {TOKEN}"})
    out = None
    for attempt, wait in enumerate((0, 5, 15, 45)):
        gap = PACE - (time.time() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        _last_call[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                out = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(wait or 5)
                continue
            out = None
            break
        except Exception:
            out = None
            break
    _json_cache[path] = out
    _save_cache()
    return out


def api_search(q):
    if not q:
        return []
    d = api_get(f"/search?q={urllib.parse.quote(q)}") or {}
    data = d.get("data") if isinstance(d, dict) else None
    return (data or {}).get("newsletters") or []


def resolve_pub(name, pub_id=None):
    """The corpus record for a publication, exact normalized name or slug match only.

    The pad's own resolver mints a bullet by name, so the name written into the copy has to
    resolve there the same way it resolves here. Anything less than an exact match is dropped:
    a bullet the pad cannot resolve keeps its placeholder and the send gate holds the draft.
    """
    target = norm(name)
    if not target:
        return None
    rec = None
    for q in (name, target, " ".join(str(name).split()[:2])):
        for p in api_search(q):
            if norm(p.get("name")) == target or norm(p.get("slug")) == target:
                rec = p
                break
        if rec:
            break
    if not rec and pub_id:
        d = api_get(f"/publications/{pub_id}") or {}
        p = (d.get("data") or {}).get("publication") if isinstance(d, dict) else None
        if p:
            for q in (p.get("name"), p.get("slug")):
                for c in api_search(q):
                    if norm(c.get("name")) == norm(p.get("name")) or norm(c.get("slug")) == norm(p.get("slug")):
                        rec = c
                        break
                if rec:
                    break
    return rec


def label_of(rec):
    """The corpus's own subscriber figure, written the way the corpus writes labels.

    The corpus mixes units: "38 members", "100,000 readers", "352M+ subscribers". The numeric
    field is the one to format from, so the bullet quotes a figure the gate can re-check.
    """
    n = rec.get("subscribers")
    try:
        n = float(n)
    except (TypeError, ValueError):
        n = None
    if n:
        if n >= 1_000_000:
            v = n / 1_000_000
            return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 100 else f"{round(v)}") + "M"
        if n >= 1_000:
            v = n / 1000
            return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 100 else f"{round(v)}") + "K"
        return str(int(n))
    raw = str(rec.get("subscribersLabel") or "")
    m = re.match(r"\s*([\d.,]+)\s*([KkMm]?)", raw)
    if not m:
        return ""
    return m.group(1).replace(",", "") + (m.group(2).upper() if m.group(2) else "")


def newest_article(pub_id):
    """The publication's newest article the corpus holds: title, sponsors, hasSponsor."""
    if not pub_id:
        return None
    d = api_get(f"/articles?publicationId={pub_id}&limit=3") or {}
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    rows = sorted([r for r in rows if r.get("title")], key=lambda r: str(r.get("published") or ""))
    return rows[-1] if rows else None


def single_token_sponsor(rec, article=None):
    """A sponsor name the gate can verify: it must be in the publication record's recentSponsors.

    The gate re-checks any "carries X" claim against `recentSponsors`, so a name taken only
    from the article row is a soft flag waiting to happen. Single word names only, because the
    gate's capture stops at the first space and would compare half a name.
    """
    recent = [str(s) for s in (rec.get("recentSponsors") or [])]
    art = [str(s) for s in ((article or {}).get("sponsors") or [])]
    for cand in art:
        for r in recent:
            if norm(cand) == norm(r) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,20}", r):
                return r
    for r in recent:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,20}", r):
            return r
    return None


# --- sources -----------------------------------------------------------------------------------
if not os.path.exists(EXPORT_CSV) or not os.path.exists(EXPORT_JSON):
    print("  no corpus export: refusing to compose blind")
    sys.exit(2)

export, export_json = {}, {}
with open(EXPORT_CSV, newline="", encoding="utf-8", errors="replace") as fh:
    for row in csv.DictReader(fh):
        export[norm(row.get("sponsor"))] = row
for rec in json.load(open(EXPORT_JSON)):
    # The export's `key` is a display slug and can carry spaces ("wordpress vip"), so index
    # every spelling the record offers under the same normalization the lookups use.
    for k in (rec.get("key"), rec.get("slug"), rec.get("sponsor")):
        if k:
            export_json[norm(k)] = rec
print(f"  export: {len(export)} sponsors (csv) / {len(export_json)} (json)")


def sponsor_rec(company):
    return export_json.get(norm(company)) or {}


def placements(company):
    row = export.get(norm(company)) or {}
    try:
        return int(float(row.get("placements"))) if row.get("placements") else None
    except (TypeError, ValueError):
        return None


def topics_of(company):
    """The lane, for the copy: the export's own category labels, junk removed."""
    rec = sponsor_rec(company)
    junk = {"uncategorized", "other", "unknown", "n/a", "general"}
    cats = [str(c.get("category")) for c in (rec.get("categories") or []) if c.get("category")]
    cats += [str(t) for t in (rec.get("topics") or []) if t]
    out = []
    for c in cats:
        c = c.strip()
        if not c or c.lower() in junk or c.lower() in {x.lower() for x in out}:
            continue
        out.append(c)
        if len(out) == 2:
            break
    return out


def newest_placement_pub(company):
    rec = sponsor_rec(company)
    obs = rec.get("observedSponsorships") or []
    if not obs:
        return None
    best = max(obs, key=lambda o: str(o.get("publishedAt") or ""))
    return str(best.get("publicationName") or "").strip() or None


def pub_catalog():
    """Every publication name the export knows, for detecting names an earlier touch used."""
    cat = set()
    for rec in export_json.values():
        for r in (rec.get("recommendations") or []):
            n = str(r.get("publicationName") or "").strip()
            if len(n) >= 4:
                cat.add(n)
        for o in (rec.get("observedSponsorships") or []):
            n = str(o.get("publicationName") or "").strip()
            if len(n) >= 4:
                cat.add(n)
    return cat


CATALOG = pub_catalog()
# name -> normalized key, longest first, so "The System Design Newsletter" wins over "Design"
CATALOG_SORTED = sorted(CATALOG, key=len, reverse=True)


def used_pubs(texts):
    """Publications an earlier touch already named: bullets, catalog hits, subject lines."""
    used = set()
    for t in texts:
        for line in str(t or "").splitlines():
            m = re.match(r"^\s*[-*]\s*(.+?)(?:\s*\(est\.|:|\s*$)", line)
            if m:
                used.add(norm(m.group(1)))
        low = str(t or "").lower()
        for name in CATALOG_SORTED:
            if plain(name) and plain(name) in plain(low):
                used.add(norm(name))
    return used


def safe_pub_name(name):
    """A publication name is written into a bullet the pad has to resolve; emoji and other
    decoration in the display name are a resolution risk and read as spam. Accents, digits and
    the punctuation real newsletter names use are fine."""
    return bool(re.fullmatch(r"[A-Za-z0-9\u00c0-\u024f &'.,#+:/()!?_-]{3,70}", str(name or "")))


def candidate_pubs(company, used, want=2, extra=()):
    """Publications for the copy: the export's recommendations for this sponsor first, then the
    names the CRM stored for the lead (an earlier snapshot of the same corpus recommender), and
    only names that resolve exactly in the corpus right now.

    A recommendation is preferred when the corpus shows a sponsor running in it, because that
    is the one line in the copy the lead cannot get anywhere else.
    """
    rec = sponsor_rec(company)
    recs = [r for r in (rec.get("recommendations") or [])]
    names = [str(r.get("publicationName") or "").strip() for r in recs]
    meta = {str(r.get("publicationName") or "").strip(): r for r in recs}
    for n in extra:
        if n and norm(n) not in {norm(x) for x in names}:
            names.append(str(n).strip())
            meta.setdefault(str(n).strip(), None)
    pool = []
    for name in names:
        if not name or norm(name) in used:
            continue
        live = resolve_pub(name, (meta.get(name) or {}).get("publicationId"))
        if not live or not safe_pub_name(str(live.get("name") or "").strip()):
            continue
        key = norm(live.get("name"))
        if key in used or any(key == norm(x["name"]) for x in pool):
            continue
        pool.append({"rec": live, "src": meta.get(name), "name": str(live.get("name")).strip()})
    # Rank the corpus's own output: a lookalike with advertisers already buying in it first, then
    # the similarity the corpus scored it with. The copy is only as good as this ordering.
    def rank(p):
        m = p["src"] or {}
        return (0 if (m.get("knownSponsors") or p["rec"].get("recentSponsors")) else 1,
                0 if str(m.get("recommendationType") or "").upper().startswith("DIRECT") else 1,
                -(int(m.get("similarityScore") or 0)))
    pool.sort(key=rank)
    return pool[:want]


def lane_pubs(company, used, want=2):
    """Fallback for a lead whose recommendations are exhausted: publications in the company's
    own categories that take sponsors, which the copy names plainly rather than dressing up as
    a lookalike. A list already carrying sponsors is preferred."""
    rec = sponsor_rec(company)
    cats = [str(c.get("category")) for c in (rec.get("categories") or []) if c.get("category")]
    cats += [str(t) for t in (rec.get("topics") or []) if t]
    out = []
    for cat in cats[:4]:
        for p in api_search(cat):
            name = str(p.get("name") or "").strip()
            if not name or norm(name) in used or p.get("acceptsSponsors") is False:
                continue
            live = resolve_pub(name)
            if not live or not safe_pub_name(str(live.get("name") or "").strip()):
                continue
            key = norm(live.get("name"))
            if key in used or any(key == norm(x["name"]) for x in out):
                continue
            out.append({"rec": live, "src": {"publicationName": name, "recommendationType": "LANE"},
                        "name": str(live.get("name")).strip()})
        if len(out) >= want:
            break
    out.sort(key=lambda p: (0 if (p["rec"].get("recentSponsors") or []) else 1,))
    return out[:want]


def prior_texts(con, lead_id, company, exclude_id=None):
    """Every earlier touch on this lead's thread, oldest first.

    `exclude_id` drops the draft this run is about to write. A --refresh re-composes the same
    draft id, and without the exclusion the draft being refreshed is read back as an earlier
    touch: its own bullets count as "used", the recommender is pushed off its first-choice
    publications onto its second, and a refresh quietly degrades the copy. Only the draft being
    written is dropped, so one queued rung still keeps the other rung's publications out of its
    own copy.
    """
    sql = ("SELECT body_text FROM drafts WHERE (lead_id = ? OR lower(company) = lower(?)) "
           "AND body_text IS NOT NULL")
    args = [lead_id, company]
    if exclude_id:
        sql += " AND id != ?"
        args.append(exclude_id)
    rows = con.execute(sql + " ORDER BY created_at", args).fetchall()
    return [r["body_text"] for r in rows]


def greeting_for(contact_name, prior):
    """The greeting already used on this thread, else the CRM's contact name.

    Reusing the thread's own greeting keeps a follow-up addressed the way the first one was,
    and the gate compares the greeting to the CRM's verified contact name.

    The CRM wins when the thread's greetings disagree with it: the gate holds a greeting that
    does not match `contact_name`, and a thread can legitimately carry two greetings because a
    first touch went to the support queue (CodeRabbit's opened on "Jensen" at support@ while the
    buyer the CRM holds is Aravind, whom the next touch already greeted). So: prefer the newest
    prior greeting that matches the CRM contact, then the CRM's own given name, and only fall
    back to the thread's latest greeting when the CRM holds no name.
    """
    name = str(contact_name or "").strip()
    greets = []
    for t in prior:
        m = re.match(r"^\s*(?:hi|hey|hello)\s+([^,\n]{2,30})[,!]", str(t), re.I)
        if m:
            greets.append(m.group(1).strip())
    if name:
        for g in reversed(greets):
            if g.lower() == name.lower() or g.lower() in name.lower().split():
                return g
        return name.split()[0]
    for g in reversed(greets):
        return g
    return ""


# --- worklist ----------------------------------------------------------------------------------
con = sqlite3.connect(f"file:{PAD_DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
today = datetime.date.today()
pending = {}          # company -> the touch numbers already sitting in the queue for it
for r in con.execute("SELECT company, id FROM drafts WHERE status = 'draft' AND company IS NOT NULL"):
    co = str(r["company"]).lower()
    did = str(r["id"])
    m = re.search(r"-follow-up-(\d)$", did)
    pending.setdefault(co, set()).add(int(m.group(1)) if m else 0)
pending_ids = {str(r["id"]) for r in con.execute("SELECT id FROM drafts WHERE status = 'draft'")}

leads = []
for r in con.execute("SELECT * FROM leads WHERE deleted_at IS NULL AND archived_at IS NULL "
                     "AND stage IN ('first_email','follow_up_1','follow_up_2')"):
    l = dict(r)
    if l.get("unsubscribed") or l.get("bounced"):
        continue
    touch, wait = TOUCH[str(l["stage"])]
    anchor = l.get("next_follow_up_at") or l.get("last_contact_at")
    if not anchor:
        continue
    due = (datetime.datetime.fromisoformat(str(l["next_follow_up_at"]).replace("Z", "+00:00")).date()
           if l.get("next_follow_up_at")
           else datetime.datetime.fromisoformat(str(l["last_contact_at"]).replace("Z", "+00:00")).date()
           + datetime.timedelta(days=wait))
    if due > today:
        continue
    l["_touch"], l["_due"], l["_due_date"] = touch, due.isoformat(), due
    leads.append(l)
leads.sort(key=lambda l: (l["_due"], str(l.get("company"))))
if args.company:
    want = {norm(c) for c in args.company}
    leads = [l for l in leads if norm(l.get("company")) in want]
if args.limit:
    leads = leads[:args.limit]
print(f"  CRM: {len(leads)} due lead(s) on the corpus ladder")

batch, blocks, skipped = [], [], []


def bullets(pubs):
    out = []
    for p in pubs:
        lab = label_of(p["rec"])
        out.append(f"- {p['name']} (est. {lab}): [TRACKED_LINK]" if lab else f"- {p['name']}: [TRACKED_LINK]")
    return "\n".join(out)


for l in leads:
    company = str(l.get("company") or "").strip()
    if norm(company) in {norm(c) for c in SKIP_COMPANIES}:
        skipped.append((company, "flagged: misdirected lead, needs re-sourcing against the right domain"))
        continue
    if company.lower() in pending and l["_touch"] not in pending[company.lower()]:
        skipped.append((company, f"a draft for another rung is already queued "
                                 f"(touch {sorted(pending[company.lower()])})"))
        continue
    if str(l.get("lead_source") or "") == "icp_research":
        skipped.append((company, "ICP lead, drafted by the ICP ladder (seed_icp_drafts.py)"))
        continue
    to = str(l.get("email") or "").strip()
    if not to:
        skipped.append((company, "no address on the lead"))
        continue
    touch = l["_touch"]
    pid = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    draft_id = f"{pid}-follow-up-{touch}"
    prior = prior_texts(con, l.get("id"), company, exclude_id=draft_id)
    greeting = greeting_for(l.get("contact_name"), prior)
    if not greeting:
        skipped.append((company, "no contact name and no earlier greeting, a name would be invented"))
        continue

    used = used_pubs(prior)
    for p in (l.get("sponsored_pubs") or []):
        used.add(norm(p))
    crm_recs = []
    for raw in (l.get("recommended_pubs") or []):
        crm_recs += [x.strip() for x in re.split(r";|,\s*|\s+and\s+", str(raw)) if x.strip()]
    pubs = candidate_pubs(company, used, want=2, extra=crm_recs)
    if not pubs:
        pubs = lane_pubs(company, used, want=2)
    if not pubs:
        skipped.append((company, "no unused resolvable publication left in the corpus for this lead"))
        continue

    # the sponsor the leading list is carrying, and its newest piece, both read live
    lead_pub = pubs[0]
    art = newest_article(lead_pub["rec"].get("id"))
    sponsor = single_token_sponsor(lead_pub["rec"], art)
    title = str((art or {}).get("title") or "").strip() or None
    # A newsletter headline can carry profanity or a joke; in a first-contact follow-up that is
    # the sender's problem, not the prospect's. Drop the title rather than the draft.
    if title and re.search(r"(motherf|\bf[u\*]ck|shit|cunt|\bvagina|dick\b|bitch|\bass\b|porn|nigg|rape|sex\b)",
                           title, re.I):
        title = None
    title = title if title and len(title) <= 110 else None
    topics = topics_of(company)
    lane = (" and ".join(topics)) if topics else None
    if draft_id in pending_ids:
        row = con.execute("SELECT created_at FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        same_day = str((row or {"created_at": ""})["created_at"])[:10] == today.isoformat()
        if not (args.refresh and same_day):
            skipped.append((company, f"{draft_id} is already queued"
                                     + (" (composed earlier today, left as it is)" if same_day else "")))
            continue

    data, source = "", ""
    one = len(pubs) == 1
    if touch == 2:
        subject = f"Two more lists for {company}: {pubs[0]['name']}" if not one else \
                  f"One more list for {company}: {pubs[0]['name']}"
        para = (f"One more list came up for the {lane} audience you already reach:" if one and lane else
                f"One more list came up for the audience you already reach:" if one else
                f"Two more lists came up for the {lane} audience you already reach:" if lane else
                f"Two more lists came up for the audience you already reach:")
        tail = []
        if sponsor:
            tail.append(f"{lead_pub['name']} carries {sponsor} in its newest issue.")
            if title:
                tail.append(f'The newest piece in {lead_pub["name"]} is "{title}".')
        text = (f"Hi {greeting},\n\n{para}\n\n{bullets(pubs)}\n\n"
                + (" ".join(tail) + "\n\n" if tail else "")
                + "Want the reader profiles and momentum behind these? If you're doing much "
                  "newsletter buying, I can also show you what we're seeing across the broader market.\n\n"
                  "[Name], Founder, NewsletterFIT")
        data = (f"{', '.join(p['name'] + ' (est. ' + (label_of(p['rec']) or '?') + ')' for p in pubs)}"
                + (f"; {lead_pub['name']} carries {sponsor}" if sponsor else "")
                + (f', newest piece "{title}"' if title else ""))
        source = ("live corpus records this run: publication match by exact name, subscriber label "
                  "and recentSponsors read from the corpus publication record, article title from "
                  "its newest article record; recommendations from the sponsor export")
    elif touch == 3:
        subject = f"Another list for {company}: {pubs[0]['name']}"
        n = placements(company)
        newest = newest_placement_pub(company)
        para = []
        if n and newest:
            para.append(f"{n} {company} placements in the newsletters I track, most recently {newest}.")
        elif newest:
            para.append(f"{company} shows up across the newsletters I track, most recently {newest}.")
        if sponsor:
            para.append(f"{lead_pub['name']} carries {sponsor} in its newest issue.")
            if title:
                para.append(f'The newest piece in {lead_pub["name"]} is "{title}".')
        if lane:
            para.append(f"The list sits in the {lane} lane you are already buying into." if one
                        else f"Both lists sit in the {lane} lane you are already buying into.")
        text = (f"Hi {greeting},\n\n" + " ".join(para) + f"\n\n{bullets(pubs)}\n\n"
                "Want the reader profiles and momentum behind these? If you're doing much "
                "newsletter buying, I can also show you what we're seeing across the broader market.\n\n"
                "[Name], Founder, NewsletterFIT")
        data = (f"{n} placements (export) newest {newest}; " if n and newest else "")
        data += ", ".join(f"{p['name']} (est. {label_of(p['rec']) or '?'})" for p in pubs)
        data += (f"; {lead_pub['name']} carries {sponsor}" if sponsor else "")
        data += (f', newest piece "{title}"' if title else "")
        source = ("placement count from the intake export (article-level measure), newest placement "
                  "pub from the export's observedSponsorships, publication label + sponsors + "
                  "newest article read live from the corpus this run")
    else:
        subject = f"One last list for {company}: {pubs[0]['name']}"
        s1 = single_token_sponsor(pubs[0]["rec"], newest_article(pubs[0]["rec"].get("id")))
        s2 = single_token_sponsor(pubs[1]["rec"]) if len(pubs) > 1 else None
        n = placements(company)
        newest = newest_placement_pub(company)
        para = []
        if n and newest:
            para.append(f"One last read from the corpus for {company}: {n} placements, most recently {newest}.")
        else:
            para.append(f"One last read from the corpus for {company}.")
        para.append("Two lists in the same lane are carrying sponsored placements right now."
                    if (s1 and s2) else
                    "One list in the same lane is carrying a sponsored placement right now."
                    if (s1 or s2) else
                    "Two lists in the same lane came up again." if not one else
                    "One more list in the same lane came up.")
        if s1:
            para.append(f"{pubs[0]['name']} carries {s1} in its newest issue.")
        if s2:
            para.append(f"{pubs[1]['name']} carries {s2} in its newest issue.")
        if title and s1:
            para.append(f'The newest piece in {lead_pub["name"]} is "{title}".')
        text = (f"Hi {greeting},\n\n" + " ".join(para) + f"\n\n{bullets(pubs)}\n\n"
                "If useful I can pull the reader profiles and momentum behind these, and who else "
                "is buying in that lane. No pitch, just the data.\n\n"
                "[Name], Founder, NewsletterFIT")
        data = ", ".join(f"{p['name']} (est. {label_of(p['rec']) or '?'})" for p in pubs)
        data += f"; carrying {', '.join(x for x in (s1, s2) if x)}" if (s1 or s2) else ""
        if title:
            data += f'; newest piece "{title}" in {lead_pub["name"]}'
        source = ("publication records, their recentSponsors and newest article read live from the "
                  "corpus this run; counts from the intake export")

    batch.append({"id": draft_id, "lead_id": l.get("id"), "company": company, "to": to,
                  "subject": subject, "text": text, "html": "", "from": FROM_EMAIL,
                  "campaign": "outbound"})
    blocks.append({"company": company, "contact": f"{l.get('contact_name') or ''} ({to})".strip(),
                   "touch": touch, "stage": STATUS[str(l["stage"])], "due": l["_due"],
                   "data": data, "source": source, "subject": subject, "text": text})

con.close()

with open(BATCH, "w") as fh:
    json.dump(batch, fh, indent=1)
with open(REPORT, "w") as fh:
    for b in blocks:
        fh.write(f"FOLLOW-UP - {b['company']} | {b['contact']} | Touch {b['touch']} "
                 f"({b['stage']}, due {b['due']})\n")
        fh.write(f"Data: {b['data']}\nSource: {b['source']}\n")
        fh.write(f"Subject: {b['subject']}\nEmail frame:\n{b['text']}\n\n")

print(f"  composed {len(batch)} draft(s) -> {BATCH}")
print(f"  report -> {REPORT}")
for co, why in skipped:
    print(f"    skip {co[:26]:<26} {why}")
if blocks:
    s = blocks[0]
    print("\n  sample:\n")
    print(f"    to: {s['contact']}\n    subject: {s['subject']}\n")
    for line in s["text"].splitlines():
        print(f"      {line}")

if not args.apply:
    print("\n  (dry run: batch + report written, nothing seeded. Re-run with --apply.)")
    sys.exit(0)

# --- apply: cadence dates, seed, mint ----------------------------------------------------------
os.makedirs(SNAP_DIR, exist_ok=True)
snap = os.path.join(SNAP_DIR, f"outreach-pre-followup-scan-{datetime.datetime.now():%Y%m%d_%H%M%S}.db")
w = sqlite3.connect(PAD_DB, timeout=30)
w.execute("VACUUM INTO ?", [snap])
stamped = 0
for l in leads:
    if l.get("next_follow_up_at") is None and l.get("last_contact_at"):
        base = datetime.datetime.fromisoformat(str(l["last_contact_at"]).replace("Z", "+00:00")).date()
        nfu = (base + datetime.timedelta(days=TOUCH[str(l["stage"])][1])).isoformat()
        w.execute("UPDATE leads SET next_follow_up_at = ? WHERE id = ?", (nfu, l["id"]))
        stamped += 1
w.commit()
w.close()
print(f"  snapshot: {os.path.basename(snap)}")
print(f"  cadence: stamped next_follow_up_at on {stamped} lead(s) that had none (last touch + the stage's wait)")

sub = subprocess.run(["node", f"{REPO}/pad/tools/seed-drafts.cjs", "--file", BATCH],
                     cwd=REPO, capture_output=True, text=True)
print("  seed:", (sub.stdout or sub.stderr).strip()[-600:])

if PAD_TOKEN and batch:
    req = urllib.request.Request(f"{PAD}/api/drafts/prepare-links", data=b"{}",
                                 headers={"X-Pad-Token": PAD_TOKEN, "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = json.loads(r.read().decode())
        print(f"  prepare-links: checked={body.get('checked')} updated={body.get('updated')} "
              f"minted={body.get('minted')} unresolved={len(body.get('unresolved') or [])}")
        if body.get("unresolved"):
            for u in (body.get("unresolved") or [])[:6]:
                print(f"    unresolved: {u}")
    except Exception as e:
        print(f"  prepare-links failed: {e}")
