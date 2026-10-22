#!/usr/bin/env python3
"""Flag sends that left the Resend account under an identity that is not ours.

This is how we found the French funnel, by accident: our pad receives the account's webhooks, so
every send in the account lands in the events table, including one we did not make. That is a
detector, not a coincidence, so it runs deliberately now and reports on its own.

Allowlist = the identities this project actually sends as. Anything else from one of our own
domains is the alarming case: it means someone can send as us.

SEND evidence only. An "open" or a click is engagement with a message that already left, and it
can arrive weeks later: the foreign campaign of 2026-09-24 kept tripping this watcher every time
one of its recipients opened an old message, long after the key behind it had been revoked. Those
events are counted separately as LEGACY (mail created before the last key rotation) and never
reported as sending.

Usage
  python3 scripts/foreign_send_watch.py            # last 3 days
  python3 scripts/foreign_send_watch.py --days 7
Exit code 0 always (a report is a report); the cron job delivers stdout.
"""
import argparse
import datetime
import json
import re
import sqlite3

DB = "/home/boxed/nf-outreach-pipeline/pad/data/outreach.db"
OUR_DOMAINS = ("newsletterfit.com", "starterlens.com")
# Who we legitimately send as. A new sender has to be added here on purpose.
ALLOWED = {
    "noreply@newsletterfit.com",     # NF product mail (verification, waitlist)
    "ian@newsletterfit.com",         # NF outreach
    "ian@starterlens.com",           # SL owner mail + audits
    "contact@starterlens.com",
    "no-reply@starterlens.com",      # SL product mail
    "hello@starterlens.com",         # SL mail-test sender (one-off, kept allowlisted)
}
# A send is evidence. An open or a click is only evidence about a message already sent.
SEND_TYPES = ("email.sent", "email.delivered", "email.delivery_delayed",
              "email.bounced", "email.complained", "email.failed")
ENGAGEMENT_TYPES = ("email.opened", "email.clicked")
# Every API key that existed before this instant has been revoked (the account's keys were
# rotated on 2026-10-01 and again on 2026-10-11). Mail created before it cannot be sent by
# anyone today, so its opens are history, not a live alarm.
ROTATION_EPOCH = "2026-10-01T00:00:00"

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=3)
args = ap.parse_args()

now = datetime.datetime.now(datetime.timezone.utc)
since = (now - datetime.timedelta(days=args.days)).isoformat()

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = list(con.execute(
    "SELECT payload, at FROM events WHERE type IN (%s) AND at >= ?"
    % ",".join("?" * len(SEND_TYPES)), [*SEND_TYPES, since]))
engagements = list(con.execute(
    "SELECT payload, at FROM events WHERE type IN (%s) AND at >= ?"
    % ",".join("?" * len(ENGAGEMENT_TYPES)), [*ENGAGEMENT_TYPES, since]))
con.close()


def sender_of(row):
    try:
        d = (json.loads(row["payload"]) or {}).get("data") or {}
    except Exception:
        return None, None, None
    frm = str(d.get("from") or "")
    m = re.search(r"<([^>]+)>", frm) or re.match(r"([^\s]+@[^\s]+)", frm)
    addr = (m.group(1) if m else "").lower().strip()
    return (addr or None), d, str(d.get("created_at") or "")


seen = {}
legacy = {}
for r in rows:
    addr, d, created = sender_of(r)
    if not addr or not isinstance(d, dict) or addr in ALLOWED:
        continue
    if created and created < ROTATION_EPOCH:
        slot = legacy.setdefault((addr, str(d.get("subject") or "")[:70]), {"n": 0, "first": str(r["at"])[:16]})
        slot["n"] += 1
        continue
    to = d.get("to") or []
    key = (addr, str(d.get("subject") or "")[:70])
    slot = seen.setdefault(key, {"n": 0, "first": str(r["at"])[:16], "to": []})
    slot["n"] += 1
    if len(slot["to"]) < 5 and isinstance(to, list):
        slot["to"] += [str(t) for t in to if t not in slot["to"]]

# Engagement on mail that is older than the last rotation: report the count, never as a send.
legacy_engagement = 0
for r in engagements:
    addr, d, created = sender_of(r)
    if addr and addr not in ALLOWED and created and created < ROTATION_EPOCH:
        legacy_engagement += 1

if legacy_engagement:
    note = (f"{legacy_engagement} open/click event(s) in the last {args.days} day(s) were "
            f"engagement with foreign mail created before {ROTATION_EPOCH} (the revoked key's "
            f"campaign); not counted as sending.")
else:
    note = ""

if not seen:
    print(f"Sender integrity: clean. {len(rows)} send event(s) in the last {args.days} day(s), "
          f"all from identities we own.")
    if note:
        print(note)
    raise SystemExit(0)

print(f"Sender integrity ALERT: {len(seen)} sending identity(ies) in the last {args.days} day(s) "
      f"are not ours.")
print()
for (addr, subject), slot in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
    own = addr.endswith(tuple("@" + d for d in OUR_DOMAINS))
    print(f"  {'SENDING AS OUR DOMAIN' if own else 'foreign sender'}: {addr}")
    print(f"    subject: {subject!r}")
    print(f"    {slot['n']} event(s), first seen {slot['first']}")
    if slot["to"]:
        print(f"    sample recipients: {', '.join(slot['to'])}")
    print()
print("  If this is not a send you made, treat the account as compromised: revoke the API key")
print("  that shows this activity in the Resend dashboard, rotate the keys we use, and check the")
print("  account's team members.")
if legacy:
    print()
    print(f"  ({sum(v['n'] for v in legacy.values())} further hit(s) are mail created before "
          f"{ROTATION_EPOCH}, i.e. the already-revoked campaign, not a live sender.)")
if note:
    print(f"  ({note})")
raise SystemExit(0)
