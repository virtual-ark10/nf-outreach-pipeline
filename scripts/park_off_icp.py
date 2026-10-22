#!/usr/bin/env python3
"""Park first-touch drafts whose lead is outside the ICP, so tomorrow does not repeat today.

The queue can hold drafts written before the fit rule existed: on 2026-10-12 the morning's
sends were Anthropic, Atlassian, AWS, Box, Brex, MongoDB, Teleport, and twenty more of the
same shape were queued for the next morning. This discards those drafts (the pad keeps the
body, status discarded, so nothing is silently destroyed) and archives the lead, which is the
flag the first-touch drafter already respects, so it cannot be composed again.

Follow-ups are left alone: a conversation already in flight is not re-judged by company size.

  python3 scripts/park_off_icp.py              # report only
  python3 scripts/park_off_icp.py --apply      # discard the drafts and archive the leads
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/home/boxed/nf-outreach-pipeline/scripts")
import icp_fit  # noqa: E402

PAD = "http://127.0.0.1:3001"
DB = "/home/boxed/nf-outreach-pipeline/pad/data/outreach.db"
FOLLOW_UP_MARKERS = ("-follow-up-", "-fu1", "-fu2", "-fu3", "-fu4")


def pad_token():
    for line in open("/home/boxed/resend-pad/.env"):
        if line.strip().startswith("PAD_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


TOKEN = pad_token()


def pad(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PAD + path, data=data, method=method,
                                headers={"X-Pad-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def queued_first_touches():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT d.id draft_id, d.company, d.to_addr, d.subject, d.lead_id, "
        "       l.id lead_id_real, l.domain, l.website, l.lead_source, l.priority, l.stage "
        "FROM drafts d LEFT JOIN leads l ON l.id = d.lead_id "
        "WHERE d.status = 'draft' ORDER BY d.created_at")]
    con.close()
    out = []
    for r in rows:
        if any(m in str(r["draft_id"]) for m in FOLLOW_UP_MARKERS):
            continue
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="discard and archive (default: report)")
    args = ap.parse_args()

    drafts = queued_first_touches()
    print(f"{len(drafts)} queued first touch(es)")
    off, kept = [], []
    for d in drafts:
        company = d.get("company") or ""
        domain = d.get("domain") or d.get("website") or ""
        fit, why, detail = icp_fit.verdict(company, domain)
        (kept if fit else off).append((d, company, domain, detail))

    print(f"\noff-ICP ({len(off)}):")
    for d, company, domain, detail in off:
        print(f"  {str(company)[:26]:26} {str(domain)[:26]:26} {detail}")
        if not args.apply:
            continue
        code, body = pad("DELETE", f"/api/drafts/{urllib.parse.quote(str(d['draft_id']))}",
                         {"reason": "off-ICP", "note": detail})
        lead_id = d.get("lead_id_real") or d.get("lead_id")
        note = (f"parked {time.strftime('%Y-%m-%d')}: outside the ICP "
                f"({detail}); first touch discarded, lead archived")
        lcode, lbody = pad("PATCH", f"/api/crm/leads/{urllib.parse.quote(str(lead_id))}",
                           {"stage": "archived", "note": note}) if lead_id else (0, {"error": "no lead"})
        print(f"      draft discard HTTP {code} | lead archive HTTP {lcode}"
              f"{'' if code == 200 and lcode == 200 else '  <-- CHECK'}")

    print(f"\nkept ({len(kept)}):")
    for d, company, domain, detail in kept:
        print(f"  {str(company)[:26]:26} {str(domain)[:26]:26} {detail}")
    if not args.apply:
        print("\n(dry run: nothing discarded. Re-run with --apply.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
