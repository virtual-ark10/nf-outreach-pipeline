#!/usr/bin/env python3
"""The ICP gap desk: import research, verify it, and say which researched copy may be drafted.

Three jobs, all deterministic, all offline except the fetch the verifier does:

    python3 scripts/icp_gap_intake.py --import AUDIT_DIR     # batch JSONs -> research/records
    python3 scripts/icp_gap_intake.py                         # report every verdict
    python3 scripts/icp_gap_intake.py --apply --check         # write verdicts.json, fail on blocked

Layout (all under `data/icp-gap/`, gitignored only for its page cache):

    research/<lead id>.json    one research record per account (schema in the skill reference)
    verify-report.json         written by scripts/icp_gap_verify.py after the quote checks
    verdicts.json              this script's output: lead id -> verified | ask-only | blocked | unresearched
    audits/<date>/             the batches, reports and verifier output an audit was run from

The seeder (`scripts/seed_icp_drafts.py`) consults the same rules live, so a run cannot draft a
researched claim that this report calls blocked. `--check` is for a wrapper or a cron job that
should shout when the tree holds copy it may not send.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gap_verdict as gv                                    # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAP = os.environ.get("ICP_GAP_DIR") or os.path.join(REPO, "data", "icp-gap")
CUSTOM = os.path.join(REPO, "data", "icp-custom")


def import_batches(audit_dir, research_dir, apply_):
    """Copy one record per company out of the audit batches, merging duplicates by name."""
    rounds = []
    for name in sorted(os.listdir(audit_dir)):
        if not name.endswith(".json") or name == "verify-report.json":
            continue
        if not (name.startswith("batch") or name.startswith("ladder")):
            continue
        path = os.path.join(audit_dir, name)
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  skip {name}: {exc}")
            continue
        if isinstance(data, dict):
            data = [data]
        for rec in data:
            if isinstance(rec, dict) and rec.get("company"):
                rounds.append((name, rec))
    merged = {}
    for source, rec in rounds:
        kid = gv.lead_id(rec["company"])
        if kid in merged:
            # Keep the richer record, but say so: the same account researched twice is a reason
            # to read both, not to let the later file silently win.
            old = merged[kid][1]
            if len(json.dumps(rec)) > len(json.dumps(old)):
                merged[kid] = (source, rec)
            print(f"  duplicate {rec['company']}: kept the fuller of {merged[kid][0]} and {source}")
        else:
            merged[kid] = (source, rec)
    os.makedirs(research_dir, exist_ok=True)
    for kid, (source, rec) in sorted(merged.items()):
        path = os.path.join(research_dir, f"{kid}.json")
        if not apply_:
            print(f"  would write {path}  ({source})")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
    print(f"  {len(merged)} research record(s) {'written to ' + research_dir if apply_ else '(dry run)'}")
    return 0


def report(research_dir, report_path, custom_dir, apply_):
    records = gv.load_research(research_dir)
    rep = gv.load_report(report_path)
    gate = gv.GapGate(custom_dir, research_dir, report_path)
    rows = []
    for kid, rec in sorted(records.items()):
        company = rec.get("company") or kid
        v = gv.verdict(rec, rep.get(str(company).strip().lower()))
        rows.append({"lead_id": kid, "company": company, **v})
    # A custom file with no record at all is the other half of the picture.
    if os.path.isdir(custom_dir):
        known = {r["lead_id"] for r in rows}
        have = {f[:-5] for f in os.listdir(custom_dir)
                if f.endswith(".json") and ".touch" not in f}
        for kid in sorted(have - known):
            rows.append({"lead_id": kid, "company": kid,
                         **gv.verdict(None, None), "custom_file": True})

    width = max([len(r["company"]) for r in rows] + [12])
    for r in rows:
        custom = "copy" if gate.has_custom({"id": r["lead_id"]}) else "    "
        print(f"  {r['status']:<12} {custom}  {r['company'][:width]:<{width}} "
              f"{r['verified']}/{r['checked']} quotes  {r['reason'][:70]}")
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n  {len(rows)} researched account(s): " +
          ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    blocked = [r for r in rows if r["status"] in (gv.BLOCKED, gv.UNRESEARCHED)]
    if apply_:
        out = os.path.join(GAP, "verdicts.json")
        os.makedirs(GAP, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({r["lead_id"]: r for r in rows}, fh, indent=1)
        print(f"  verdicts -> {out}")
    return 1 if blocked else 0


def main():
    ap = argparse.ArgumentParser(description="import, verify and report ICP gap research")
    ap.add_argument("--import", dest="import_dir", metavar="AUDIT_DIR",
                    help="import batch/ladder JSON records from this audit directory")
    ap.add_argument("--research", default=os.path.join(GAP, "research"))
    ap.add_argument("--report", default=os.path.join(GAP, "verify-report.json"))
    ap.add_argument("--custom", default=CUSTOM)
    ap.add_argument("--apply", action="store_true", help="write records / verdicts.json")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when copy exists whose research is blocked or missing")
    args = ap.parse_args()

    if args.import_dir:
        import_batches(args.import_dir, args.research, args.apply)
    rc = report(args.research, args.report, args.custom, args.apply)
    return rc if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
