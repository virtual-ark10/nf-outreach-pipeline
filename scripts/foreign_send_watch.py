#!/usr/bin/env python3
"""Flag sends that left the Resend account under an identity that is not ours.

This is how we found the French funnel, by accident: our pad receives the account's webhooks, so
every send in the account lands in the events table, including one we did not make. That is a
detector, not a coincidence, so it runs deliberately now and reports on its own.

Allowlist = the identities this project actually sends as. Anything else from one of our own
domains is the alarming case: it means someone can send as us.

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
SENT_TYPES = ("email.sent", "email.delivered", "email.bounced", "email.complained",
              "email.failed", "email.opened", "email.clicked", "email.delivery_delayed")

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=3)
args = ap.parse_args()

since = (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(days=args.days)).isoformat()

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
q = ("SELECT payload, at FROM events WHERE type IN (%s) AND at >= ?"
     % ",".join("?" * len(SENT_TYPES)))
rows = list(con.execute(q, [*SENT_TYPES, since]))
con.close()

seen = {}
for r in rows:
    try:
        d = (json.loads(r["payload"]) or {}).get("data") or {}
    except Exception:
        continue
    frm = str(d.get("from") or "")
    m = re.search(r"<([^>]+)>", frm) or re.match(r"([^\s]+@[^\s]+)", frm)
    addr = (m.group(1) if m else "").lower().strip()
    if not addr:
        continue
    if addr in ALLOWED:
        continue
    to = d.get("to") or []
    key = (addr, str(d.get("subject") or "")[:70])
    slot = seen.setdefault(key, {"n": 0, "first": str(r["at"])[:16], "to": []})
    slot["n"] += 1
    if len(slot["to"]) < 5 and isinstance(to, list):
        slot["to"] += [str(t) for t in to if t not in slot["to"]]

if not seen:
    print(f"Sender integrity: clean. {len(rows)} send event(s) in the last {args.days} day(s), "
          f"all from identities we own.")
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
raise SystemExit(0)
