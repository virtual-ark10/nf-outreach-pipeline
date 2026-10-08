#!/usr/bin/env python3
"""seed_icp_drafts.py — the ICP ladder: drafts for the leads the corpus holds no placement for.

An ICP lead came out of the lookalike scan (icp_research.py): a company, a GTM contact, its
industry, its size, its city. What it does NOT have is a sponsor record. The export the
first-touch generator reads (sponsor-leads.csv) never logged this company buying anything, so
"saw you in X" would be a fabrication and there is no placement count to quote. Of the 84 ICP
leads in the CRM exactly one appears in that export at all.

What the corpus CAN say about such a company is who buys in its lane and which lists take
sponsors, so that is the whole ladder: the lists the company's market already validated, the
sponsors running in them, and the reader profiles behind them. Four touches on the same 3/7/14
clock the CRM already runs (DUE_DAYS in crm_lead_state.py). Unlike the corpus ladder there is
no prospect-specific dig to do, so no model drafts this: every figure in the copy is read from
the corpus record it stands next to.

  1  first_email   the lists taking sponsors in their lane, and who is already buying in them
  2  follow_up_1   one list in detail: how often it goes out, to how many, who runs in it
  3  follow_up_2   two more lists, from the other side of the lane
  4  follow_up_3   the close, one list attached so the mail still carries a tracked link

Composing is inert (a draft in the pad queue, which the 09:00 send gate then verifies), but the
ladder stays OFF until data/icp-drafts.enabled exists, so a new sequence gets read before it can
reach an inbox. Nothing here sends mail: the gate is the only send path.

Usage
  python3 scripts/seed_icp_drafts.py --sample 3        # print the copy, touch nothing
  python3 scripts/seed_icp_drafts.py --touch 2 --sample 2
  python3 scripts/seed_icp_drafts.py --apply           # compose, seed, mint tracked links
"""
import argparse
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
CORPUS_ENV = "/home/boxed/.config/newsletterfit/corpus.env"
BATCH = "/home/boxed/.hermes/cache/scratch/nf_icp_batch.json"
# The ladder is off until this file exists: a new sequence is reviewed before it can send.
FLAG = f"{REPO}/data/icp-drafts.enabled"
PAD = "http://127.0.0.1:3001"

# Which lists to search for, by the lead's industry. The industry is a coarse firmographic label
# from the people lane (34 of the 84 leads are "Software Development"), so the queries take it
# down to what the lead actually sells. The first matching row wins; the last row is the floor.
LANES = [
    (r"data infrastructure|data analytics|analytics|big data",
     "data infrastructure", ["data infrastructure", "data engineering", "analytics engineering"]),
    (r"business intelligence|business analytics",
     "business intelligence", ["business intelligence", "product analytics"]),
    (r"market research|information service",
     "market research", ["market research", "consumer insights"]),
    (r"it service|consulting|outsourc",
     "infrastructure and cloud", ["cloud infrastructure", "devops", "platform engineering"]),
    (r"software|technology|internet|computer",
     "software and data tooling", ["developer tools", "software engineering", "product analytics"]),
]
DEFAULT_LANE = ("data and software", ["data engineering", "product analytics", "developer tools"])

# The touch number as the ladder names it, and the CRM stage that touch belongs to.
TOUCH_STAGES = {1: "leads", 2: "first_email", 3: "follow_up_1", 4: "follow_up_2"}


def norm(v):
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def load_env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# --- lane and candidate selection (pure, so the tests need no network) ----------------------

def lane_for(lead):
    """(label, [queries]) for a lead: its own market, not the corpus's taxonomy of it."""
    industry = str((lead or {}).get("industry") or "")
    for pattern, label, queries in LANES:
        if re.search(pattern, industry, re.I):
            return label, queries
    return DEFAULT_LANE


def accepts_sponsors(pub):
    """Only a list that takes sponsors is worth naming: the pitch is 'back what already works'."""
    return pub.get("acceptsSponsors") is True


def subscribers_of(pub):
    try:
        return int(pub.get("subscribers") or 0)
    except (TypeError, ValueError):
        return 0


def pick(pub, query):
    """The parts of a corpus record the copy is allowed to stand on."""
    sponsors = [str(s).strip() for s in (pub.get("recentSponsors") or []) if str(s).strip()]
    return {
        "name": str(pub.get("name") or "").strip(),
        "slug": str(pub.get("slug") or "").strip(),
        "label": str(pub.get("subscribersLabel") or "").replace(" subscribers", "").strip(),
        "subscribers": subscribers_of(pub),
        "sponsors": sponsors,
        "mentions": int(pub.get("sponsorMentions") or 0),
        "frequency": str(pub.get("frequency") or "").strip(),
        "category": str(pub.get("category") or "").strip(),
        "lane_query": query,
    }


def usable(p):
    return bool(p["name"] and p["slug"] and p["label"])


def candidates(search, lane_queries, exclude=()):
    """Every sponsor-taking list the lane's queries return, best first.

    The corpus's own result order is the only relevance signal there is for a company it has
    never logged, so it is preserved as the discovery order. Audience size is deliberately NOT
    the sort key: ranking by it floated a 354K general-career list above a 23K data-engineering
    one, and a list the corpus has actually logged sponsors in is the honest lead, so those come
    first (stable, so the order inside each group stays the corpus's).
    """
    seen, out = set(), []
    for q in lane_queries:
        try:
            body = search(q) or {}
        except Exception:
            continue
        for pub in (body.get("newsletters") or []):
            if not accepts_sponsors(pub):
                continue
            p = pick(pub, q)
            if not usable(p) or p["slug"].lower() in seen or norm(p["name"]) in exclude:
                continue
            seen.add(p["slug"].lower())
            out.append(p)
    proven = [p for p in out if p["sponsors"] or p["mentions"]]
    claimed = [p for p in out if not (p["sponsors"] or p["mentions"])]
    return proven + claimed


def rotate(picks, offset, want):
    """A window that walks the pool, so a batch of lookalikes is not all sent the same names."""
    if not picks:
        return []
    n = len(picks)
    return [picks[(offset + i) % n] for i in range(min(want, n))]


# --- the copy -------------------------------------------------------------------------------

def _greeting(lead):
    return str((lead.get("contact_name") or "")).strip().split()[0]


def _bullets(picks):
    return "\n".join(
        f"- {p['name']} (est. {p['label']}): [TRACKED_LINK]" if p["label"]
        else f"- {p['name']}: [TRACKED_LINK]" for p in picks)


def _lead_in(lane, picks):
    word = {1: "One", 2: "Two", 3: "Three"}.get(len(picks), str(len(picks)))
    return f"{word} {lane} lists taking sponsors:"


def first_touch(lead, picks):
    """Touch 1: the lists their market already validated, and who is already buying in one."""
    first, company = _greeting(lead), str(lead.get("company") or "").strip()
    lane, _ = lane_for(lead)
    peer_pick = next((p for p in picks if p["sponsors"]), None)
    peer = (f"{peer_pick['sponsors'][0]} is buying in {peer_pick['name']}, one of them."
            if peer_pick else "")
    subject = (f"{peer_pick['sponsors'][0]} is buying in {peer_pick['name']}"
               if peer_pick else f"the {lane} lists taking sponsors")
    text = (
        f"Hi {first},\n\n"
        "I run NewsletterFIT. We track sponsorships across newsletters and watch which lists "
        "are rising, so a team can back the ones its own market has already validated.\n\n"
        + (f"{peer}\n\n" if peer else "")
        + f"{_lead_in(lane, picks)}\n\n"
        + f"{_bullets(picks)}\n\n"
        f"Reader profiles and momentum behind any of them are in the corpus. Happy to pull the "
        f"two or three that fit {company}.\n\n"
        "[Name], Founder, NewsletterFIT"
    )
    return subject, text


def follow_up(lead, touch, picks):
    """Touches 2 to 4. Each one adds something the last did not say; none refers back."""
    first, company = _greeting(lead), str(lead.get("company") or "").strip()
    lane, _ = lane_for(lead)
    p = picks[0] if picks else {}
    if touch == 2:
        # The list the first touch led on, in detail. New information, not a nudge.
        freq = str(p.get("frequency") or "").strip()
        sponsors = p.get("sponsors") or []
        runs = ""
        if sponsors:
            runs = (f", and {sponsors[0]} runs placements in it" if len(sponsors) == 1
                    else f", and {', '.join(sponsors)} run placements in it")
        reach = (f"goes out {freq} to {p['label']} readers" if freq
                 else f"reaches {p['label']} readers")
        subject = f"who reads {p['name']}"
        text = (
            f"Hi {first},\n\n"
            f"{p['name']} {reach}{runs}.\n\n"
            "If a list with that audience is worth a look, I can send the reader profile behind "
            "it: who they are, what they click, and how the list is trending.\n\n"
            "[Name], Founder, NewsletterFIT"
        )
        return subject, text
    if touch == 3:
        # The other end of the lane, with the sponsors that run there.
        named = "; ".join(f"{q['name']} carries {', '.join(q['sponsors'])}"
                          for q in picks if q["sponsors"])
        subject = f"the other side of {lane}"
        text = (
            f"Hi {first},\n\n"
            f"Same shape, other end of {lane}:\n\n"
            + f"{_bullets(picks)}\n\n"
            + (f"{named}.\n\n" if named else "")
            + f"If {company} is closer to these buyers, I can pull the profiles behind them "
              "the same way.\n\n"
              "[Name], Founder, NewsletterFIT"
        )
        return subject, text
    # Touch 4, the close. One list stays attached so the mail still carries a tracked link,
    # and nothing here apologises or points back at an earlier message: the gate holds both.
    subject = "leaving it here"
    text = (
        f"Hi {first},\n\n"
        f"I will leave it here. If newsletter placements make it onto the roadmap at {company}, "
        "reply and I will pull the two or three lists that fit your buyers best.\n\n"
        "The one I would start with:\n\n"
        + f"- {p['name']} (est. {p['label']}): [TRACKED_LINK]\n\n"
        "[Name], Founder, NewsletterFIT"
    )
    return subject, text


def compose(lead, touch, picks):
    if touch == 1:
        return first_touch(lead, picks)
    return follow_up(lead, touch, picks)


def draft_id(lead, touch):
    slug = re.sub(r"[^a-z0-9]+", "-", str(lead.get("company") or "").lower()).strip("-")
    return slug if touch == 1 else f"{slug}-follow-up-{touch}"


# --- CRM + corpus I/O (only ever reached from main) -----------------------------------------

def search_factory():
    env = load_env(CORPUS_ENV)
    api, token = env["NEWSLETTERFIT_API"], env["API_BEARER_TOKEN"]

    def search(q):
        req = urllib.request.Request(f"{api}/search?q={urllib.parse.quote(q)}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode()).get("data", {})
    return search


def pending_leads(today, require_email=True):
    """ICP leads owed a touch: uncontacted at 'leads', or past their cadence date.

    A lead with no address cannot be drafted, a suppressed one must not be, and a stage whose
    window has not opened yet is left for the run that owns it: the gate would hold it anyway.
    `require_email=False` is for --sample only, so the copy can be read before an address
    exists (all 84 ICP leads start without one; discovery fills them at 08:00).
    """
    con = sqlite3.connect(f"file:{PAD_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    where = ("WHERE COALESCE(lead_source,'') = 'icp_research' "
             "AND deleted_at IS NULL AND archived_at IS NULL "
             "AND COALESCE(unsubscribed,0) = 0 AND COALESCE(bounced,0) = 0")
    if require_email:
        where += " AND COALESCE(email,'') <> ''"
    rows = [dict(r) for r in con.execute(f"SELECT * FROM leads {where}")]
    con.close()
    out = []
    for l in rows:
        stage = str(l.get("stage") or "")
        if stage == "leads":
            out.append((1, l))
            continue
        touch = next((t for t, s in TOUCH_STAGES.items() if s == stage and t > 1), None)
        if not touch:
            continue
        due = str(l.get("next_follow_up_at") or "")[:10]
        if due and due <= today:
            out.append((touch, l))
    out.sort(key=lambda t: (-(t[1].get("score") or 0), str(t[1].get("company"))))
    return out


def sample_leads(count):
    """The top ICP leads as they stand, whatever stage: for previewing the copy only."""
    con = sqlite3.connect(f"file:{PAD_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM leads WHERE COALESCE(lead_source,'')='icp_research' "
        "AND deleted_at IS NULL AND archived_at IS NULL ORDER BY score DESC, company LIMIT ?",
        [count])]
    con.close()
    return rows


def queue_index():
    try:
        token = load_env("/home/boxed/resend-pad/.env")["PAD_TOKEN"]
        req = urllib.request.Request(f"{PAD}/api/drafts", headers={"X-Pad-Token": token})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
        rows = body.get("data") if isinstance(body, dict) else body
        return {str(x.get("id")): x for x in (rows or []) if isinstance(x, dict)}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser(description="compose the ICP ladder into the pad queue")
    ap.add_argument("--apply", action="store_true", help="seed the drafts and mint links")
    ap.add_argument("--sample", type=int, default=0, help="print N composed drafts and stop")
    ap.add_argument("--touch", type=int, default=0, choices=[0, 1, 2, 3, 4],
                    help="only this touch (with --sample)")
    ap.add_argument("--count", type=int, default=40, help="most drafts to compose this run")
    ap.add_argument("--force", action="store_true", help="compose even when the ladder is off")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    if not args.sample and not (args.force or os.path.exists(FLAG)):
        print(f"  ICP ladder is off: create {FLAG} (or --force) once the copy is approved")
        return 0

    search = search_factory()
    if args.sample and args.touch:
        # The copy depends on the lead and the corpus record, not on the stage, so a preview of
        # touch N can stand on the top ICP leads even though none has been contacted yet.
        work = [(args.touch, l) for l in sample_leads(args.count)]
    else:
        work = pending_leads(today, require_email=not args.sample)
        if args.touch:
            work = [(t, l) for t, l in work if t == args.touch]
    print(f"  {len(work)} ICP lead(s) owed a touch")

    pools, batch, skipped = {}, [], []
    for i, (touch, lead) in enumerate(work[: args.count]):
        lane, queries = lane_for(lead)
        if lane not in pools:
            pools[lane] = candidates(search, queries)
        picks = rotate(pools[lane], i, 3 if touch != 4 else 1)
        if not picks:
            skipped.append((lead.get("company"), f"no sponsor-taking list found in {lane}"))
            continue
        subject, text = compose(lead, touch, picks)
        if not _greeting(lead):
            skipped.append((lead.get("company"), "no contact name, a greeting would be invented"))
            continue
        batch.append({
            "id": draft_id(lead, touch), "lead_id": lead.get("id"), "company": lead.get("company"),
            "to": lead.get("email"), "subject": subject, "text": text, "html": "",
            "from": load_env(f"{REPO}/pad/.env").get("FROM_EMAIL") or "ian@newsletterfit.com",
            "campaign": "outbound", "touch": touch,
        })

    print(f"  composed {len(batch)} draft(s)   (skipped {len(skipped)})")
    for co, why in skipped[:8]:
        print(f"    skip {str(co)[:26]:<26} {why}")

    if args.sample:
        for d in batch[: args.sample]:
            print(f"\n  --- touch {d['touch']}  id={d['id']}  to={d['to']}")
            print(f"  subject: {d['subject']}\n")
            for line in d["text"].splitlines():
                print(f"    {line}")
        return 0

    held = queue_index()
    fresh = [d for d in batch if d["id"] not in held]
    for d in batch:
        if d["id"] in held:
            print(f"    held  {d['id']} already in the queue, left as it is")
    with open(BATCH, "w") as fh:
        json.dump(fresh, fh, indent=1)
    print(f"  {len(fresh)} draft(s) -> {BATCH}")

    if not args.apply:
        print("  (dry run: batch written, nothing seeded. Re-run with --apply to seed.)")
        return 0

    env = load_env("/home/boxed/resend-pad/.env")
    if fresh:
        sub = subprocess.run(["node", f"{REPO}/pad/tools/seed-drafts.cjs", "--file", BATCH],
                             cwd=REPO, capture_output=True, text=True)
        print("  seed:", (sub.stdout or sub.stderr).strip()[-300:])
    req = urllib.request.Request(f"{PAD}/api/drafts/prepare-links", data=b"{}",
                                headers={"X-Pad-Token": env["PAD_TOKEN"],
                                         "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        print("  prepare-links:", r.read().decode()[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
