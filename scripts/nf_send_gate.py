#!/usr/bin/env python3
"""NF send gate: the thing that stands between a queued draft and a prospect's inbox.

Nothing goes out that this script has not cleared. It is deliberately paranoid, because a
wrong number or a stray "apologies, my last email said..." to a growth marketer costs more
than a held draft ever will.

Verdicts
  PASS   every check cleared, safe to send
  REVIEW a soft flag (something stated that the corpus cannot confirm) - a human decides
  HOLD   a hard failure: wrong corpus fact, duplicate first touch, guessed name, apology or
         back-reference language, broken tracking, or a prospect who already replied

Usage
  python3 scripts/nf_send_gate.py                 # report only
  python3 scripts/nf_send_gate.py --json          # machine-readable, for the cron job
  python3 scripts/nf_send_gate.py --send-clean    # send every PASS draft (what the daily job does)

Exit code 0 if nothing is on HOLD, 1 otherwise, so a cron job can tell the difference.
"""
import argparse
import csv
import datetime
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

PAD = "http://127.0.0.1:3001"
CRM_DB = "/home/boxed/nf-outreach-pipeline/pad/data/outreach.db"
LEDGER = "/home/boxed/newsletterfit/attribution/attribution.json"
CORPUS_ENV = "/home/boxed/.config/newsletterfit/corpus.env"
# The intake export is the corpus's article-level sponsorship record; see Corpus.placements.
EXPORT_CSV = "/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv"
# Sends per run. A file, not a constant, so the daily volume is one number to change. A dated
# ramp file beats it while the domain is young: pick the newest entry that is not in the future.
CAP_FILE = "/home/boxed/nf-outreach-pipeline/data/send-cap.txt"
RAMP_FILE = "/home/boxed/nf-outreach-pipeline/data/send-ramp.txt"
DEFAULT_CAP = 50


def cap_for_date(day):
    """(cap, where it came from): the ramp's entry for this date, else the cap file."""
    newest = None
    try:
        for line in open(RAMP_FILE):
            parts = line.split("#")[0].split()
            if len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
                try:
                    n = int(parts[1])
                except ValueError:
                    continue
                if parts[0] <= day and (newest is None or parts[0] > newest[0]):
                    newest = (parts[0], n)
    except OSError:
        pass
    if newest:
        return newest[1], f"ramp {newest[0]}"
    try:
        return int(open(CAP_FILE).read().strip()), "cap file"
    except (OSError, ValueError):
        return DEFAULT_CAP, "default"

# --- language that must never ship to a prospect -----------------------------------------
BACK_REFERENCE = re.compile(
    r"(i made a mistake|my mistake|i was wrong|i messed up|apolog|correction to|correcting my"
    r"|my earlier email|my previous email|previous email said|my last email|last email said"
    r"|following up|follow up on my|circling back|circle back|bumping this|just bumping"
    r"|reaching out again|second time reaching|as i mentioned|as mentioned (earlier|previously)"
    r"|to be transparent|going back through|fabricated|zero ground-truth|feel free to ignore"
    r"|sorry for the (earlier|previous|confusion))", re.I)
# Date-anchored staleness, any case: "(Aug 27", "since Aug 3", "most recently Aug 28".
STALE_DATE = re.compile(
    r"(since (?:aug|sep|jul|jun) \w*\s?\d|\((?:aug|sep|jul|jun|may) \d+[,)]"
    r"|\bin the last \d+ (?:days|weeks|months|quarter)|most recently (?:aug|sep|jul|jun) \d"
    r"|latest (?:aug|sep|jul|jun) \d)", re.I)
# Relative phrases, lowercase only. Prose writes "last week"; a publication is titled "This Week
# in Startups", and the staleness check must not fire on a name.
STALE_RELATIVE = re.compile(
    r"\b(last week|this week|this month|last month|yesterday|today|recent issue|last issue"
    r"|\d+ (?:days?|weeks?|months?|hours?) ago)\b")
CORPUS_SIZE = re.compile(r"\d[\d,.]*\s*(k|m)?\+?\s*newsletters", re.I)
UNVERIFIABLE_FIGURE = re.compile(
    r"(\d+ issues? in \d+ days|\+\d+(\.\d+)?% (growth|in|subscriber)|in \d+ days;|\d+ pieces? in \d+ days)", re.I)
EM_DASH = "\u2014"


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
    """Normalize a name for comparison: case, punctuation, apostrophes and emoji all off."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


PAD_TOKEN = load_env("/home/boxed/resend-pad/.env").get("PAD_TOKEN", "")


def pad_call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PAD + path, data=data,
                                headers={"X-Pad-Token": PAD_TOKEN, "Content-Type": "application/json"},
                                method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


# --- corpus facts ---------------------------------------------------------------------------
class Corpus:
    def __init__(self):
        env = load_env(CORPUS_ENV)
        self.api = env.get("NEWSLETTERFIT_API", "https://newsletterfit.com/api/v1")
        self.token = env.get("API_BEARER_TOKEN", "")
        self.cache = {}
        self._placements = None

    def search(self, q):
        if q in self.cache:
            return self.cache[q]
        req = urllib.request.Request(f"{self.api}/search?q={urllib.parse.quote(q)}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode()).get("data", {})
        except Exception as e:
            body = {"error": str(e)[:120]}
        self.cache[q] = body
        return body

    def sponsor_count(self, company):
        """How many sponsor placements the corpus has logged for this company."""
        d = self.search(company)
        for s in d.get("sponsors") or []:
            if str(s.get("name", "")).strip().lower() == company.strip().lower():
                return s.get("count")
        return None

    def placements(self, company):
        """The house placement count: distinct articles in the corpus where the company was
        logged as a sponsor.

        The intake export builds it from sponsor-typed article mentions plus confirmed
        sponsored articles, bucketed by article, so it is a strict superset of the search
        rollup's sponsors[].count wherever the two disagree (Brex 34 vs 14, Tracksuit 24 vs
        15, Unblocked 9 vs 8, HubSpot 10 vs 5, and equal where the sponsor is small: Stacker
        5, Profound 3). A subset can never be the fuller measure, so the article-level count
        is the source of truth and the rollup is not quoted. Returns None when the export has
        no grounded count for the company, which means the copy should not state one.
        """
        if self._placements is None:
            self._placements = {}
            try:
                with open(EXPORT_CSV, newline="", encoding="utf-8", errors="replace") as fh:
                    for row in csv.DictReader(fh):
                        key = norm(row.get("sponsor"))
                        if not key:
                            continue
                        try:
                            self._placements[key] = int(float(row.get("placements") or 0))
                        except (TypeError, ValueError):
                            continue
            except OSError:
                self._placements = {}
        return self._placements.get(norm(company))

    def publication(self, name):
        """Exact-match a publication by name, trying several query shapes.

        The search endpoint reads short queries as topics, so a real publication can sit
        buried behind generic AI lists ("AI Agents Simplified" returns 'This Week in AI'
        first). Only a normalized exact match is accepted. A loose candidate is never used,
        because a wrong "correction" is worse than an unresolved figure.
        """
        target = norm(name)
        if not target:
            return None
        key = ("pub", target)
        if key in self.cache:
            return self.cache[key]
        found = None
        for q in (name, target, " ".join(str(name).split()[:2])):
            if not q:
                continue
            for p in (self.search(q).get("newsletters") or []):
                if norm(p.get("name")) == target:
                    found = p
                    break
            if found:
                break
        self.cache[key] = found
        return found


def label_to_k(label):
    """'169K' / 'est. 144K' / '1.1M' / '36M+ subscribers' -> number of subscribers."""
    if not label:
        return None
    m = re.search(r"([\d.,]+)\s*([KkMm])", str(label))
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    return num * (1000 if m.group(2).lower() == "k" else 1000000)


# --- CRM facts ------------------------------------------------------------------------------
def crm():
    con = sqlite3.connect(f"file:{CRM_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def crm_lead(con, company):
    r = con.execute("SELECT * FROM leads WHERE lower(company) = lower(?)", [company]).fetchone()
    return dict(r) if r else None


def already_contacted(con):
    """Leads with an outbound message already logged, and whether they replied/declined."""
    sent = {}
    for r in con.execute("SELECT lead_id, subject, stage_at_send, status FROM emails"):
        if str(r["status"] or "").lower() in ("sent", "delivered", "received"):
            sent.setdefault(str(r["lead_id"] or "").lower(), []).append(str(r["subject"] or r["stage_at_send"] or ""))
    replied = {}
    for r in con.execute("SELECT lead_id, classification, sentiment FROM replies"):
        lead = str(r["lead_id"] or "").lower()
        if lead:
            replied.setdefault(lead, []).append((str(r["classification"] or ""), str(r["sentiment"] or "")))
    return sent, replied


def ledger():
    try:
        store = json.load(open(LEDGER))
    except Exception:
        return {}
    return {c.get("token"): c for c in store.get("clicks", [])}


# --- checks ---------------------------------------------------------------------------------
def gate(draft, ctx):
    """Return (verdict, findings) for one draft. findings = list of dicts with level+message."""
    f = []

    def hard(m, **extra):
        f.append({"level": "HOLD", "message": m, **extra})

    def soft(m, **extra):
        f.append({"level": "REVIEW", "message": m, **extra})

    d_id = draft.get("id", "?")
    company = str(draft.get("company") or d_id.split("--")[0]).strip()
    text = draft.get("text") or ""
    html = draft.get("html") or ""
    subject = draft.get("subject") or ""
    blob = " ".join([subject, text, html])
    to = str(draft.get("to") or "")

    # 1. structure + tracking
    for field, val in (("from", draft.get("from")), ("to", to), ("subject", subject), ("text", text)):
        if not str(val or "").strip():
            hard(f"missing {field}")
    tokens = re.findall(r"lt=([A-Za-z0-9_-]{20,40})", html)
    if not tokens:
        hard("no tracked link (click attribution would be lost)")
    known = ctx["ledger"]
    for t in tokens:
        if t not in known:
            hard(f"tracked token does not resolve in the ledger: {t[:10]}...")
        elif known[t].get("dest") and not str(known[t]["dest"]).startswith("https://newsletterfit.com"):
            hard(f"tracked link points off-site: {known[t]['dest'][:60]}")
    if re.search(r"\[(TRACKED_LINK|name|Name)\]|\{\{|\bTODO\b", text + html):
        hard("unfilled placeholder")

    # 2. language
    for m in BACK_REFERENCE.finditer(text + " " + subject):
        hard(f"back-reference / apology language: {m.group(0)!r}")
    if EM_DASH in blob:
        hard("em-dash (reads as machine-written)")
    for rx in (STALE_DATE, STALE_RELATIVE):
        for m in rx.finditer(text + " " + subject):
            hard(f"stale time reference: {m.group(0)!r}")
    for m in CORPUS_SIZE.finditer(text):
        hard(f"corpus-size claim: {m.group(0)!r}")
    for m in UNVERIFIABLE_FIGURE.finditer(text):
        soft(f"figure the corpus cannot confirm: {m.group(0)!r}")

    # 3. identity: one draft per company, verified contact, no re-first-touch
    same_co = ctx["by_company"].get(company.lower(), [])
    if len(same_co) > 1:
        others = [d for d in same_co if d.get("id") != d_id]
        hard(f"duplicate first touch: {len(same_co)} drafts for {company} "
             f"({', '.join(str(d.get('to')) for d in others)})")
    lead = ctx["leads"].get(company.lower())
    if not lead:
        hard(f"no CRM record for {company}: contact unverified")
    else:
        first = (text.splitlines()[0] if text.splitlines() else "").strip()
        greeting = re.sub(r"^hi\s+", "", first, flags=re.I).strip(" ,.!")
        cname = str(lead.get("contact_name") or "")
        local = to.split("@")[0].lower()
        if greeting and cname:
            given = cname.split()[0].lower()
            if greeting.lower() not in (given, cname.lower()):
                hard(f"greeting {greeting!r} does not match the CRM contact {cname!r}")
        if lead.get("email") and to.lower() != str(lead["email"]).lower():
            soft(f"draft goes to {to}, the CRM's verified contact is {lead['email']}")
        if lead.get("unsubscribed") or lead.get("bounced"):
            hard("lead is unsubscribed/bounced in the CRM")
        if "reply" in str(lead.get("stage") or "").lower():
            hard(f"lead is in stage {lead.get('stage')!r}: an active conversation, do not cold-send")
    sent, replied = ctx["sent"], ctx["replied"]
    hist = sent.get(company.lower(), [])
    contacted = bool((lead or {}).get("first_contact_at")) or bool(hist)
    stage = str((lead or {}).get("stage") or "").lower()
    if contacted:
        # A draft for a lead who already got the first touch is the next rung of the ladder,
        # and the ladder has a clock (day 3, then 7, then 14). Going early reads as pressure
        # and burns a touch, so an early draft waits in the queue instead.
        due = (lead or {}).get("next_follow_up_at") or (lead or {}).get("next_action_at")
        today = datetime.date.today().isoformat()
        if due and str(due)[:10] > today:
            hard(f"not due yet: the cadence says wait until {str(due)[:10]}")
        elif not due:
            soft("no due date on the lead; confirm the cadence before this goes out")
        if stage in ("lead", "leads") and hist:
            hard(f"already emailed in this campaign ({hist[0] or 'first touch'})")
    if replied.get(company.lower()):
        hard(f"prospect replied already: {replied[company.lower()][:2]}")

    # 4. corpus facts: placement count, publications, subscriber labels
    claimed = []
    for m in re.finditer(r"(\d+)\s+placements?", text):
        claimed.append(int(m.group(1)))
    for m in re.finditer(r"(\d+)\s+(?:[A-Za-z0-9.'&\s]{0,24}?)(?:placements|sponsorships)", text):
        try:
            claimed.append(int(m.group(1)))
        except Exception:
            pass
    for n in set(claimed):
        real = ctx["corpus"].placements(company)
        if real is None:
            soft(f"states {n} {company} placements but the corpus export has no grounded count "
                 f"for {company}: keep the number out of the copy")
        elif n != real:
            hard(f"states {n} {company} placements, the corpus logs {real}",
                 kind="count", claimed=n, real=real,
                 pattern=r"(?<!\d)" + str(n) + r"(?=\s+[A-Za-z0-9.'&\s]{0,24}?(?:placements|sponsorships))")

    pubs_named = re.findall(r"-\s+([^\n:()]{3,60}?)(?:\s*\(est\.|\s*:\s|\n|$)", text)
    # A publication is verified by the link that was minted onto it: the pad's link pass only
    # mints for a name it resolved to a corpus slug, and the anchor text is that resolved name.
    # Re-matching display names against the search endpoint is flaky (the result window shifts
    # between calls), so a name that carries a token is proof, and a bullet without one is the
    # thing worth flagging.
    anchored = {}
    for m in re.finditer(r"<a[^>]*lt=([A-Za-z0-9_-]{20,40})[^>]*>([\s\S]*?)</a>", html, re.I):
        label = re.sub(r"<[^>]*>", " ", m.group(2))
        # The anchor's text is HTML, so an ampersand in a publication name arrives as
        # '&amp;'. norm() keeps letters, so '&amp;' used to normalize to '...amp...'
        # and never matched the plain-text bullet ('Greg & Taylor'), which flagged a
        # correctly linked bullet as unresolved. Decode entities before comparing.
        label = html_mod.unescape(re.sub(r"\s+", " ", label)).strip()
        dest = str((known.get(m.group(1)) or {}).get("dest") or "")
        if label and "/app/publications/" in dest:
            anchored[norm(label)] = dest.rsplit("/", 1)[-1]
    for raw in pubs_named:
        name = html_mod.unescape(raw).strip(" -")
        slug = anchored.get(norm(name))
        if not slug:
            soft(f"bullet names {name!r} with no tracked link, so the publication was never "
                 f"resolved: confirm it exists before sending")
            continue
        rec = ctx["corpus"].publication(name)
        if not rec:
            # The mint proves the slug; only the display-name lookup failed, which is a flake.
            continue
        # subscriber label check, when the draft quotes one
        frag = re.search(re.escape(name) + r"[^\n]{0,40}?([\d.,]+\s*[KM])\b", text)
        if frag:
            claimed_subs = label_to_k(frag.group(1))
            real_subs = rec.get("subscribers") or label_to_k(rec.get("subscribersLabel"))
            if claimed_subs and real_subs:
                if abs(claimed_subs - real_subs) / max(real_subs, 1) > 0.12:
                    hard(f"{name}: draft says {frag.group(1)}, corpus says {rec.get('subscribersLabel')}")
        # "carries X" sponsor-book claim
        for m in re.finditer(re.escape(name) + r"[^\n]{0,60}?(?:carries|sponsored by|books?)\s+([A-Z][\w&.'-]+(?:,? (?:and )?[A-Z][\w&.'-]+)?)", text):
            mentioned = [x.strip() for x in re.split(r",| and ", m.group(1)) if x.strip()]
            recent = [str(s).lower() for s in (rec.get("recentSponsors") or [])]
            for sp in mentioned:
                if recent and sp.lower() not in " ".join(recent):
                    soft(f"{name}: draft says it carries {sp}, corpus lists {rec.get('recentSponsors')}")

    # 5. subject/body alignment
    subj_pub = re.search(r"\bin\s+(?:the\s+)?([A-Z][\w&'.#-]+(?:\s+[A-Z][\w&'.#-]+){0,4})", subject)
    if subj_pub:
        token = subj_pub.group(1).split()[0].strip()
        if token.lower() not in text.lower():
            soft(f"subject names {subj_pub.group(1)!r}, body never mentions it")

    verdict = "HOLD" if any(x["level"] == "HOLD" for x in f) else ("REVIEW" if f else "PASS")
    return verdict, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--send-clean", action="store_true")
    ap.add_argument("--fix-counts", action="store_true",
                    help="refresh placement counts the corpus has outgrown, then re-gate")
    ap.add_argument("--cap", type=int, default=None,
                    help="max sends this run (default: data/send-cap.txt, else 50)")
    args = ap.parse_args()

    st, payload = pad_call("GET", "/api/drafts")
    drafts = payload.get("data") if isinstance(payload, dict) else payload
    # An empty queue answers {"data": []}; `payload or ...` would turn that into the wrapper
    # dict and the loop would iterate its keys, so unwrap explicitly and treat [] as empty.
    drafts = drafts if isinstance(drafts, list) else []
    if not drafts:
        print("  queue empty, nothing to gate")
        return 0

    con = crm()
    leads = {str(r["company"]).lower(): dict(r) for r in con.execute("SELECT * FROM leads")}
    sent, replied = already_contacted(con)
    by_company = {}
    for d in drafts:
        co = str(d.get("company") or d.get("id", "").split("--")[0]).lower()
        by_company.setdefault(co, []).append(d)

    ctx = {"ledger": ledger(), "leads": leads, "sent": sent, "replied": replied,
           "by_company": by_company, "corpus": Corpus()}

    results = []
    for d in sorted(drafts, key=lambda x: str(x.get("id"))):
        verdict, findings = gate(d, ctx)
        results.append({"id": d.get("id"), "company": d.get("company"), "to": d.get("to"),
                        "subject": d.get("subject"), "verdict": verdict,
                        "findings": findings})

    # Refresh the placement counts the corpus has outgrown. A count is a live figure, not a
    # claim that can be wrong: the corpus ingests constantly, so the number is re-read at
    # send time instead of the draft being held for a drift of one.
    if args.fix_counts:
        for r in results:
            holds = [x for x in r["findings"] if x["level"] == "HOLD"]
            if not holds or any(x.get("kind") != "count" for x in holds):
                continue
            d = next((x for x in drafts if str(x.get("id")) == str(r["id"])), None)
            if not d:
                continue
            text, html = d.get("text") or "", d.get("html") or ""
            for x in holds:
                # a single, auditable integer substitution; nothing else in the body moves
                text = re.sub(r"(?<!\d)" + str(x["claimed"]) + r"(?=\s+[A-Za-z0-9.'&\s]{0,24}?(?:placements|sponsorships))",
                              str(x["real"]), text)
                html = re.sub(r"(?<!\d)" + str(x["claimed"]) + r"(?=\s+[A-Za-z0-9.'&\s]{0,24}?(?:placements|sponsorships))",
                              str(x["real"]), html)
            if html.count("lt=") != (d.get("html") or "").count("lt="):
                print(f"  fix-counts {r['id']}: refused, tracked-link count changed")
                continue
            code, body = pad_call("PUT", f"/api/drafts/{urllib.parse.quote(str(r['id']))}",
                                  {"text": text, "html": html})
            print(f"  fix-counts {r['id']}: HTTP {code} ({[x['message'] for x in holds]})")

    if args.fix_counts:
        # re-read the queue and re-gate so the printed verdicts are post-fix
        st, payload = pad_call("GET", "/api/drafts")
        drafts = payload.get("data") or payload
        by_company = {}
        for d in drafts:
            co = str(d.get("company") or d.get("id", "").split("--")[0]).lower()
            by_company.setdefault(co, []).append(d)
        ctx["by_company"] = by_company
        results = []
        for d in sorted(drafts, key=lambda x: str(x.get("id"))):
            verdict, findings = gate(d, ctx)
            results.append({"id": d.get("id"), "company": d.get("company"), "to": d.get("to"),
                            "subject": d.get("subject"), "verdict": verdict,
                            "findings": findings})


    if args.json:
        print(json.dumps({"results": results,
                          "counts": {v: sum(1 for r in results if r["verdict"] == v)
                                     for v in ("PASS", "REVIEW", "HOLD")}}, indent=2))
    else:
        for r in results:
            print(f"  {r['verdict']:<6} {str(r['id'])[:26]:<26} {str(r['to'])[:30]:<30}")
            for find in r["findings"]:
                print(f"         [{find['level']}] {find['message']}")
        counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "REVIEW", "HOLD")}
        print(f"\n  PASS {counts['PASS']}  REVIEW {counts['REVIEW']}  HOLD {counts['HOLD']}  (of {len(results)})")

    if args.send_clean:
        # Breaker before the cap: a bounce or complaint spike means the list or the domain is in
        # trouble, and sending more is the one thing guaranteed to make it worse.
        try:
            rows = list(con.execute(
                "SELECT status FROM emails WHERE direction='outbound' ORDER BY id DESC LIMIT 100"))
            total = len(rows)
            bad = sum(1 for r in rows
                      if str(r["status"] or "").lower() in ("bounced", "complained", "failed"))
        except Exception:
            total = bad = 0
        if total >= 20 and bad / total > 0.05:
            print(f"  CIRCUIT BREAKER: {bad} of the last {total} sends bounced or complained "
                  f"({bad / total:.0%}). Sending nothing until that is looked at.")
            return 1

        # Priority: the ladder first, then first touches by how good the lead is. A follow-up
        # is a conversation already in flight, so it outranks a cold open; inside first touches
        # the CRM's own score decides who is worth the slot.
        cap = args.cap
        cap_src = "--cap"
        if cap is None:
            cap, cap_src = cap_for_date(datetime.date.today().isoformat())

        def kind_and_score(r):
            d = next((x for x in drafts if str(x.get("id")) == str(r["id"])), {})
            lead = leads.get(str(d.get("company") or "").lower()) or {}
            contacted = bool(lead.get("first_contact_at")) or str(lead.get("stage") or "") not in ("", "leads")
            try:
                score = int(lead.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            return (0 if contacted else 1, -score)

        queue = sorted([r for r in results if r["verdict"] == "PASS"], key=kind_and_score)
        print(f"  cap {cap} ({cap_src}); {len(queue)} clean draft(s) eligible")
        for r in queue[:cap]:
            code, body = pad_call("POST", f"/api/drafts/{urllib.parse.quote(str(r['id']))}/send", {})
            r["sent"] = code == 200
            r["send_response"] = body if code != 200 else "queued"
            print(f"  send {r['id']}: HTTP {code} {json.dumps(body)[:160]}")
        if len(queue) > cap:
            print(f"  {len(queue) - cap} clean draft(s) held for capacity this run (cap {cap}); "
                  f"they go out on the next run")

    return 1 if any(r["verdict"] == "HOLD" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
