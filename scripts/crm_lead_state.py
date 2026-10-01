#!/usr/bin/env python3
"""The CRM's own answer to "who is due for the next touch", and a repair for the rows that
cannot answer it yet.

The lead sheet is retired: the CRM is the record of truth, so the follow-up scan reads this
instead of a CSV that drifts. Two repairs are needed first, both consequences of the move to
SQLite:

  * the September batch has no outbound `sent_at` (only `status_at`), so the cadence cannot
    compute and the due date is null with it;
  * those same leads have no `last_contact_at`, so even with the mail rows fixed the lead
    row has no anchor to count from.

Both are backfilled from data the CRM already holds. Nothing is invented, and the send dates
used are the recorded status timestamps.

Usage
  python3 scripts/crm_lead_state.py             # report: repair needed + who is due
  python3 scripts/crm_lead_state.py --apply     # apply the backfill, then report
"""
import argparse
import datetime
import os
import sqlite3
import sys

DB = "/home/boxed/nf-outreach-pipeline/pad/data/outreach.db"
SNAP_DIR = "/home/boxed/nf-outreach-pipeline/pad/snapshots"
DUE_DAYS = {"first_email": 3, "follow_up_1": 4, "follow_up_2": 7, "follow_up_3": 0}
STATUS = {"leads": "Not contacted", "first_email": "First Email", "follow_up_1": "Follow 2",
          "follow_up_2": "Follow 3", "follow_up_3": "Follow 4",
          "replied": "Replied", "won": "Won", "no": "No"}
GRACE_DAYS = 7          # a window that closed more than this long ago is expired, not overdue

today = datetime.date.today()
ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

con = sqlite3.connect(DB, timeout=30)
con.row_factory = sqlite3.Row

mail_gap = con.execute("SELECT COUNT(*) FROM emails WHERE direction='outbound' AND sent_at IS NULL").fetchone()[0]
lead_gap = con.execute("""SELECT COUNT(*) FROM leads l WHERE l.last_contact_at IS NULL
                          AND EXISTS (SELECT 1 FROM emails e WHERE e.lead_id = l.id AND e.direction='outbound')""").fetchone()[0]
print(f"  repair needed: {mail_gap} outbound row(s) without a send date, {lead_gap} contacted lead(s) without a contact date")

if args.apply and (mail_gap or lead_gap):
    os.makedirs(SNAP_DIR, exist_ok=True)
    snap = os.path.join(SNAP_DIR, f"outreach-pre-backfill-{datetime.datetime.now():%Y%m%d_%H%M%S}.db")
    con.execute("VACUUM INTO ?", [snap])
    con.execute("""UPDATE emails SET sent_at = COALESCE(status_at, created_at)
                   WHERE direction='outbound' AND sent_at IS NULL AND COALESCE(status_at, created_at) IS NOT NULL""")
    con.execute("""UPDATE leads SET
                     first_contact_at = COALESCE(first_contact_at, (SELECT MIN(e.sent_at) FROM emails e
                                              WHERE e.lead_id = leads.id AND e.direction='outbound' AND e.sent_at IS NOT NULL)),
                     last_contact_at  = COALESCE(last_contact_at,  (SELECT MAX(e.sent_at) FROM emails e
                                              WHERE e.lead_id = leads.id AND e.direction='outbound' AND e.sent_at IS NOT NULL))
                   WHERE last_contact_at IS NULL
                     AND EXISTS (SELECT 1 FROM emails e WHERE e.lead_id = leads.id AND e.direction='outbound' AND e.sent_at IS NOT NULL)""")
    con.commit()
    print(f"  backfilled (snapshot first: {os.path.basename(snap)})")

leads = [dict(r) for r in con.execute("SELECT * FROM leads WHERE deleted_at IS NULL AND archived_at IS NULL")]
con.close()

def due_for(lead):
    stage = str(lead.get("stage") or "")
    wait = DUE_DAYS.get(stage)
    anchor = lead.get("next_follow_up_at") or lead.get("last_contact_at")
    if not wait or not anchor:
        return None
    d = datetime.datetime.fromisoformat(str(anchor).replace("Z", "+00:00")).date()
    return d + datetime.timedelta(days=wait)

inflight, expired, upcoming = [], [], []
for l in leads:
    d = due_for(l)
    row = {"company": l.get("company") or "", "contact": l.get("contact_name") or "",
           "email": l.get("email") or "", "role": l.get("contact_role") or "",
           "stage": str(l.get("stage") or ""), "status": STATUS.get(str(l.get("stage")), str(l.get("stage"))),
           "last": str(l.get("last_contact_at") or "")[:10],
           "due": d.isoformat() if d else ""}
    if d is None:
        continue
    if d < today - datetime.timedelta(days=GRACE_DAYS):
        expired.append(row)
    elif d <= today:
        inflight.append(row)
    else:
        upcoming.append(row)

print(f"\n=== DUE now, the scan acts on these ({len(inflight)}) ===")
for r in sorted(inflight, key=lambda x: x["due"]):
    print(f"  {r['company'][:22]:<22} {r['status']:<12} {r['contact'][:18]:<18} {r['role'][:8]:<8} last {r['last']} due {r['due']}")

print(f"\n=== due later, no touch yet ({len(upcoming)}) ===")
for r in sorted(upcoming, key=lambda x: x["due"])[:12]:
    print(f"  {r['company'][:22]:<22} {r['status']:<12} last {r['last']} due {r['due']}")

print(f"\n=== window closed, do NOT send a late touch ({len(expired)}) ===")
for r in expired:
    print(f"  {r['company'][:22]:<22} {r['status']:<12} last {r['last']} window closed {r['due']}")

print("\n  A lead here is due when its stage's wait has elapsed since the last touch; an expired\n"
      "  window means the ladder ran out (day 14), so the honest move is to close it out, not to\n"
      "  fire a three-week-late final note.")
sys.exit(0)
