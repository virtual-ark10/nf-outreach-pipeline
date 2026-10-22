"""Does a researched gap earn its place in the queue?

A custom gap file (`data/icp-custom/<lead id>.json`) is hand-written copy built on research. That
research has a record (`data/icp-gap/research/<lead id>.json`, schema in the skill's
`references/icp-gap-research.md`) and the record's quotes are checked mechanically by
`scripts/icp_gap_verify.py`, which writes `verify-report.json`. This module is the one place that
decides whether those three line up, so the answer is the same in the seeder, a report and a test.

The rule the whole thing exists for: copy may only assert that a vendor lacks something when the
vendor's own documentation says what it ingests and the capability is absent from it, and every
quote used as evidence was found verbatim on the page it cites. A paraphrase is a claim, and a
claim sent to a prospect is a liability, not a personalization.

Verdicts:

  verified      confirmed gap, every cited quote verified. The researched copy may go.
  ask-only      the gap is unverified (the docs are silent), quotes verified. The copy may go only
                in the "offer the data, ask the question" form, never as an assertion.
  blocked       do not use this copy and do not fall back to the lane template either: the premise
                is gone (gap disconfirmed), or the evidence behind a confirmed gap failed
                verification, or there is no research record at all. Hold the lead and say why.
  unchecked     the quotes could not be fetched (403, 429, timeout), so nothing is refuted and
                nothing is cleared. Held, with the fix named: re-run the verifier.
  unresearched  a custom file exists with no research record behind it. Blocked in practice, but
                named separately so the report can say how many unaudited files are in the tree.
"""

import json
import os
import re

VERIFIED, ASK_ONLY, BLOCKED, UNRESEARCHED, UNCHECKED = (
    "verified", "ask-only", "blocked", "unresearched", "unchecked")

# A failed fetch is not a refutation. The verifier records why a quote did not check out, and the
# difference decides whether an account is blocked or simply not checked yet: a 403 or a 429 from
# the vendor (or from the rendered-fetch fallback) is a transient fetch failure, while "not found"
# with a leading-match count means the sentence genuinely is not on that page. Collapsing the two
# would either hold good accounts for ever or let a paraphrase through.
_UNCHECKED = ("firecrawl failed", "not retried", "timed out", "timed out", "__fetch_failed__",
              "http error", "url error")


def classify(row):
    """verified | refuted | unchecked for one row of a verify report."""
    if row.get("ok"):
        return "verified"
    reason = str(row.get("reason") or "").lower()
    if any(marker in reason for marker in _UNCHECKED):
        return "unchecked"
    return "refuted"


def _split(entry):
    rows = list((entry or {}).get("rows") or [])
    return ([r for r in rows if classify(r) == "refuted"],
            [r for r in rows if classify(r) == "unchecked"])


def lead_id(company):
    """The lead id convention the CRM and the custom-copy files share."""
    return re.sub(r"[^a-z0-9]+", "-", str(company or "").lower()).strip("-")


def load_research(research_dir):
    """{lead id: research record}. A file that will not parse is kept as a broken record so the
    verdict is 'blocked' rather than a silent absence."""
    out = {}
    if not os.path.isdir(research_dir):
        return out
    for name in sorted(os.listdir(research_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(research_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError) as exc:
            out[name[:-5]] = {"company": name[:-5], "_broken": str(exc)}
            continue
        if isinstance(rec, list):            # a batch file dropped straight in
            for item in rec:
                if isinstance(item, dict) and item.get("company"):
                    out[lead_id(item["company"])] = item
            continue
        if isinstance(rec, dict) and rec.get("company"):
            out[lead_id(rec["company"])] = rec
    return out


def load_report(path):
    """{company name: {checked, verified, rows}} from the verifier, keyed loosely by name."""
    try:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {str(k).strip().lower(): v for k, v in (report or {}).items() if isinstance(v, dict)}


def _report_for(company, report):
    return report.get(str(company or "").strip().lower())


def verdict(record, report_entry=None):
    """The decision for one research record. Pure: no disk, no network, so a test can pin it."""
    if record is None:
        return {"status": UNRESEARCHED, "reason": "no research record behind this copy",
                "gap_status": None, "checked": 0, "verified": 0, "failed": []}
    if record.get("_broken"):
        return {"status": BLOCKED, "reason": f"research record will not parse: {record['_broken']}",
                "gap_status": None, "checked": 0, "verified": 0, "failed": []}

    gap = str(record.get("gap_status") or "").strip().lower()
    company = record.get("company")
    refuted, unchecked = _split(report_entry)
    checked = int((report_entry or {}).get("checked") or 0)
    good = int((report_entry or {}).get("verified") or 0)
    base = {"gap_status": gap or None, "checked": checked, "verified": good,
            "failed": refuted + unchecked}

    # A quote that is not on the page it cites is a false claim in the research, whatever the gap
    # status says, and it can never be sent.
    if refuted:
        first = refuted[0]
        return dict(base, status=BLOCKED,
                    reason=(f"{len(refuted)} quote(s) not found on the cited page, "
                            f"first: {str(first.get('url'))[:50]} {str(first.get('reason'))[:60]}"))

    if gap == "disconfirmed":
        return dict(base, status=BLOCKED,
                    reason="gap disconfirmed: they already have the capability, or they are supply-side")

    # Every quote checked out but the fetch failed: not a refutation, and not a clearance either.
    # The fix is to re-run the verifier, so the account is held rather than argued with.
    if unchecked:
        return dict(base, status=UNCHECKED,
                    reason=f"{len(unchecked)} quote(s) could not be fetched; re-run the verifier")

    if gap == "confirmed":
        if report_entry is None:
            return dict(base, status=BLOCKED,
                        reason="gap called confirmed with no quote verification on record")
        if checked == 0:
            return dict(base, status=BLOCKED,
                        reason="gap called confirmed with no quotes cited")
        return dict(base, status=VERIFIED,
                    reason=f"confirmed gap, {good}/{checked} quotes verified")

    if gap == "unverified":
        if checked == 0:
            return dict(base, status=BLOCKED,
                        reason="gap unverified and no quote was checked, so not even the premise is audited")
        return dict(base, status=ASK_ONLY,
                    reason="gap unverified: offer the data and ask, never assert absence")

    return dict(base, status=BLOCKED, reason=f"gap_status is {gap or 'missing'}, which is not a verdict")


def custom_body_hit(custom_dir, lead_id_, text, min_probe=40):
    """The custom-copy file stem whose body appears in this draft, or None.

    The seeder checks the research before it composes, but a draft already in the queue keeps the
    body it was composed with, and the queue is where mail actually waits. So the send path asks a
    different question: is this text a researched body, and whose? Matching on the body (not on the
    draft id) means a draft renamed or re-seeded since still gets found. A probe shorter than
    `min_probe` characters is not evidence: it could be a common sentence.
    """
    kid = str(lead_id_ or "")
    if not kid or not text or not os.path.isdir(custom_dir):
        return None
    haystack = re.sub(r"\s+", " ", text).strip().lower()
    for name in sorted(os.listdir(custom_dir)):
        if not name.endswith(".json"):
            continue
        stem = name[:-5]
        if stem != kid and not stem.startswith(kid + ".touch"):
            continue
        try:
            with open(os.path.join(custom_dir, name), encoding="utf-8") as fh:
                body = str((json.load(fh) or {}).get("body") or "")
        except (OSError, ValueError):
            continue
        probe = re.sub(r"\s+", " ", body).strip().lower()[:80]
        if len(probe) >= min_probe and probe in haystack:
            return stem
    return None


class GapGate:
    """Ledger for one drafting run: which custom copies are allowed, and why not for the rest."""

    def __init__(self, custom_dir, research_dir, report_path):
        self.custom_dir = custom_dir
        self.research = load_research(research_dir)
        self.report = load_report(report_path)
        self._cache = {}

    def has_custom(self, lead, touch=1):
        kid = str(lead.get("id") or "")
        name = f"{kid}.json" if touch == 1 else f"{kid}.touch{int(touch)}.json"
        return os.path.exists(os.path.join(self.custom_dir, name))

    def verdict_for(self, lead_id_, company):
        """The verdict for a lead whether or not a custom file exists, for callers that already
        know the copy is in play (the send gate sees a body, not a file path)."""
        kid = str(lead_id_ or "")
        if kid in self._cache:
            return self._cache[kid]
        record = self.research.get(kid) or self.research.get(lead_id(company))
        v = verdict(record, _report_for((record or {}).get("company") or company, self.report))
        v["lead_id"] = kid
        v["company"] = company
        self._cache[kid] = v
        return v

    def check(self, lead, touch=1):
        """None when there is no custom copy to judge, else the verdict for this lead."""
        if not self.has_custom(lead, touch):
            return None
        return self.verdict_for(lead.get("id"), lead.get("company"))

    def summary(self):
        counts = {}
        for v in self._cache.values():
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        return counts
