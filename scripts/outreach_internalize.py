#!/usr/bin/env python3
"""Internalize outreach links — newsletters must never send valuable leads to external sites.

Sweeps the Pad draft queue (the SQLite-backed one, read over the pad's API), and for every outreach link whose stored
destination is an EXTERNAL publication site (substack.com, thedigitalcreator.co, ...),
Resolves the publication to its NewsletterFIT internal page via the corpus search API,
mints a NEW token via POST /api/v1/outreach/links (server-side — no file merge or
API restart needed), and rewrites the draft (text + html) to use the internal token.

Policy: all outreach links are internal. The lead stays on newsletterfit.com and the
nf_attr cookie + click counts still attribute the visit — no traffic is given away.

Outputs:
  - the rewritten drafts pushed back to the pad (PUT /api/drafts/:id)
  - attribution store (home) mirrored with the minted tokens (dedupe cache)
  - attribution.new-tokens.json — audit log of tokens minted this run (server already
    holds them; no merge/restart required)
  - a report line per swap; unresolved pubs are reported and left untouched

Usage:
  python3 outreach_internalize.py [--pad http://127.0.0.1:3001] [--store path] [--dry-run]
Env (or --flag): NEWSLETTERFIT_API, API_BEARER_TOKEN, NF_BASE_URL, PAD_URL, PAD_TOKEN
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# Old local mints were 32-lowercase-hex; the API mints base64url tokens
# ([A-Za-z0-9_-], 22-24 chars observed: 16 or 18 random bytes, unpadded).
TOKEN_RE = re.compile(r"lt=([A-Za-z0-9_-]{20,40})")
DRAFT_LINK_RE = re.compile(r"^-\s*(.+?)\s*[—–-]\s*.*?https://newsletterfit\.com/api/click\?lt=([A-Za-z0-9_-]{20,40})", re.M | re.I)
OLD_LOCAL_HEX_RE = re.compile(r"^[a-f0-9]{32}$")
TAG_RE = re.compile(r"<[^>]+>")


def is_api_token(tok):
    """True for server-side (POST /outreach/links) tokens.

    Discriminator: old local-mint tokens are 32 chars of pure lowercase hex and
    are DEAD server-side (/api/click 404s). API tokens are base64url (mixed
    case, 22-32 chars) — any such token is server-known.
    """
    tok = tok or ""
    if OLD_LOCAL_HEX_RE.match(tok):
        return False  # old local-mint format
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{22,32}", tok))


ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.S | re.I)


def wrap_links(html, link):
    """Wrap every BARE occurrence of `link` in <a href="link">link</a>.

    Already-anchored occurrences (inside an <a>…</a>) are left untouched, so
    re-runs and pre-written anchors never get double-wrapped.
    """
    masks = []

    def hold(m):
        masks.append(m.group(0))
        return f"\x00{len(masks) - 1}\x00"

    tmp = ANCHOR_RE.sub(hold, html)
    tmp = tmp.replace(link, f'<a href="{link}">{link}</a>')
    for i, m in enumerate(masks):
        tmp = tmp.replace(f"\x00{i}\x00", m)
    return tmp


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# ---------------------------------------------------------------- pad queue
# Since the SQLite cutover (2026-09-10) the draft queue is rows in the pad's
# database, NOT data/drafts.json — and that retired file still sits on disk, so
# reading it would "succeed" on a stale copy while the live queue never changed.
# The queue is therefore read and written over the pad's own API.
PAD_ENV = "/home/boxed/resend-pad/.env"
PAD_BASE = os.environ.get("PAD_URL", "http://127.0.0.1:3001")


def pad_token():
    """The pad token: env first, then the pad's .env (never printed)."""
    tok = (os.environ.get("PAD_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(PAD_ENV, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PAD_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _pad_json(req):
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def pad_drafts(base, token):
    """The LIVE draft queue (the pad returns only status='draft' rows)."""
    return _pad_json(urllib.request.Request(
        base.rstrip("/") + "/api/drafts", headers={"X-Pad-Token": token})).get("data", [])


def pad_put_draft(base, token, draft_id, fields):
    """Save edits onto an existing draft. The pad merges, so sending only the
    changed fields is enough — and a 404 means the draft is no longer in the
    queue (sent or discarded in the pad), which must not be papered over."""
    req = urllib.request.Request(
        base.rstrip("/") + "/api/drafts/" + urllib.parse.quote(str(draft_id)),
        data=json.dumps(fields).encode("utf-8"), method="PUT",
        headers={"X-Pad-Token": token, "Content-Type": "application/json"})
    return _pad_json(req)


def read_live_queue(base):
    """The live queue, or a hard exit. An empty read must never be mistaken for
    'nothing to do' — that is exactly how a stale copy fools you."""
    token = pad_token()
    if not token:
        print(f"FATAL: PAD_TOKEN not found (env or {PAD_ENV})", file=sys.stderr)
        sys.exit(1)
    try:
        drafts = pad_drafts(base, token)
    except Exception as e:                      # refused, 401, proxy down …
        print(f"FATAL: could not read the draft queue from {base}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"queue: {base.rstrip('/')}/api/drafts — {len(drafts)} pending draft(s)")
    return drafts


def save_changed_drafts(base, drafts, before):
    """Push back only the drafts this run actually changed. A draft that is gone
    from the queue (sent or discarded in the pad meanwhile) 404s and is reported
    as a failure rather than silently dropped. Returns (saved, failed)."""
    token = pad_token()
    saved = failed = 0
    for d in drafts:
        did = d.get("id")
        if before.get(did) == (d.get("text"), d.get("html")):
            continue
        try:
            pad_put_draft(base, token, did, {"text": d.get("text"), "html": d.get("html")})
            saved += 1
        except Exception as e:
            failed += 1
            print(f"  [WARN] could not save {did}: {e}", file=sys.stderr)
    return saved, failed


def load_env(env_path):
    if not os.path.exists(env_path):
        return {}
    out = {}
    for line in open(env_path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def api_mint(api_base, api_token, lead_id, dest, link_ref, campaign=None):
    """Mint a tracking link server-side: POST /api/v1/outreach/links.

    Returns the stored row (snake_case, mirrors the API response). Raises on
    validation errors / non-2xx so callers can report the mint as unresolved.
    """
    sep = "" if api_base.endswith("/") else "/"
    url = f"{api_base}{sep}outreach/links"
    payload = {"leadId": lead_id, "dest": dest, "ref": link_ref}
    if campaign:
        payload["campaign"] = campaign
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read().decode())
    d = (resp or {}).get("data") or {}
    if not d.get("token") or not d.get("link"):
        raise RuntimeError(f"unexpected mint response: {resp}")
    return {
        "token": d["token"],
        "lead_id": d.get("lead_id", lead_id),
        "campaign": d.get("campaign", campaign or "default"),
        "link_ref": d.get("link_ref", link_ref),
        "dest": d.get("dest", dest),
        "created_at": d.get("created_at")
        or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "expires_at": d.get("expires_at")
        or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 30 * 86400)),
        "first_click_at": None,
    }


def api_search(query, api_base, api_token):
    sep = "" if api_base.endswith("/") else "/"
    url = f"{api_base}{sep}search?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = json.loads(r.read().decode())
    return ((payload.get("data") or {}).get("newsletters")) or []


def pick_pub(query, dest_host, results):
    """Best match among search results. Returns (slug, name) or None."""
    q = norm(query)
    qhost = norm(dest_host.split(".")[0] if dest_host else "")
    scored = []
    for r in results:
        name = r.get("name") or ""
        slug = r.get("slug") or ""
        sc = 0
        if norm(name) == q:
            sc = 100
        elif norm(slug) == q:
            sc = 95
        elif norm(name) and q and (q in norm(name) or norm(name) in q):
            sc = 80
        elif qhost and (norm(slug) == qhost or norm(slug).startswith(qhost)):
            sc = 85
        if sc:
            scored.append((sc, slug, name))
    scored.sort(key=lambda t: -t[0])
    return (scored[0][1], scored[0][2]) if scored else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad", default=PAD_BASE, help="the pad's base URL (its /api/drafts is the queue)")
    ap.add_argument("--store", default="/home/boxed/newsletterfit/attribution/attribution.json")
    ap.add_argument("--out-tokens", default="/home/boxed/newsletterfit/attribution/attribution.new-tokens.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env("/home/boxed/.config/newsletterfit/corpus.env")
    api_base = os.environ.get("NEWSLETTERFIT_API") or env.get("NEWSLETTERFIT_API", "http://127.0.0.1:3000")
    api_token = os.environ.get("API_BEARER_TOKEN") or env.get("API_BEARER_TOKEN", "")
    base_url = os.environ.get("NF_BASE_URL", "https://newsletterfit.com")
    if not api_token:
        print("FATAL: API_BEARER_TOKEN not found", file=sys.stderr)
        sys.exit(1)

    drafts = read_live_queue(args.pad)
    before = {d.get("id"): (d.get("text"), d.get("html")) for d in drafts}
    store = json.load(open(args.store, encoding="utf-8"))
    clicks = store.get("clicks", [])
    by_token = {c["token"]: c for c in clicks}

    # map (lead_id, pub-dest) -> existing API-minted internal token so we don't
    # mint duplicates. Only API tokens count — old local 32-hex rows are dead.
    existing_internal = {}
    for c in clicks:
        if is_api_token(c.get("token")) and c.get("dest", "").startswith(base_url):
            existing_internal[(c.get("lead_id"), c.get("dest"))] = c

    new_tokens = []
    report = []
    unresolved = []

    for draft in drafts:
        lead_id = draft.get("id") or draft.get("company") or "?"
        text = draft.get("text", "") or ""
        html = draft.get("html", "") or ""
        pairs = set(TOKEN_RE.findall(text)) | set(TOKEN_RE.findall(html))

        for tok in pairs:
            row = by_token.get(tok)
            if not row:
                unresolved.append((lead_id, tok, "token not in store"))
                continue
            dest = row.get("dest", "")
            if dest.startswith(base_url) and is_api_token(tok):
                report.append((lead_id, tok, dest, "already-internal"))
                continue
            if dest.startswith(base_url):
                # Old local-mint token (32-hex): the server store doesn't know it,
                # so /api/click would 404. Re-mint the SAME internal dest server-side.
                internal_dest = dest.rstrip("/")
                slug = internal_dest.rsplit("/", 1)[-1]
                pub_name = slug
            else:
                link_ref = row.get("link_ref", "")
                # Pubs are listed as "- Name — 18K subs, ... — https://...lt=TOKEN".
                # Extract the name by splitting each bullet on the em-dash, so we search
                # by the HUMAN name rather than re-parsing via regex on the whole body.
                query = ""
                for line in text.splitlines():
                    if tok in line and line.lstrip().startswith("-"):
                        parts = [p.strip() for p in re.split(r"\s*—\s*|\s*–\s*", line, maxsplit=3)]
                        if len(parts) >= 2 and parts[0].lstrip().startswith("-") and parts[0].lstrip().lstrip("-").strip():
                            query = parts[0].lstrip().lstrip("-").strip()
                            break
                if not query:
                    query = link_ref.replace("pub-", "").replace("-", " ")
                try:
                    results = api_search(query, api_base, api_token)
                except Exception as e:
                    unresolved.append((lead_id, tok, f"search failed: {e}"))
                    continue
                host = urllib.parse.urlparse(dest).netloc if dest else ""
                picked = pick_pub(query, host, results)
                if not picked:
                    unresolved.append((lead_id, tok, f"no internal page for '{query}'"))
                    continue
                slug, pub_name = picked
                internal_dest = f"{base_url}/app/publications/{slug}"
            key = (lead_id, internal_dest)
            if key in existing_internal:
                nt = existing_internal[key]
            elif args.dry_run:
                unresolved.append((lead_id, tok, "DRY-RUN: would mint server-side"))
                continue
            else:
                try:
                    nt = api_mint(
                        api_base, api_token, lead_id, internal_dest,
                        f"pub-{slug}-nf",
                        row.get("campaign") or "default",
                    )
                except Exception as e:
                    unresolved.append((lead_id, tok, f"mint failed: {e}"))
                    continue
                existing_internal[key] = nt
                clicks.append(nt)
                new_tokens.append(nt)
            old_link = f"{base_url}/api/click?lt={tok}"
            new_link = f"{base_url}/api/click?lt={nt['token']}"
            if old_link in text:
                text = text.replace(old_link, new_link)
            if old_link in html:
                # HTML body: bare tracking URL becomes a clickable anchor.
                # Already-anchored occurrences are left untouched (no double wrap).
                html = html.replace(old_link, new_link)
                html = wrap_links(html, new_link)
            status = "RE-MINTED" if dest.startswith(base_url) else "SWAPPED"
            report.append((lead_id, pub_name, nt["dest"], status))

        draft["text"] = text
        draft["html"] = html

    if not args.dry_run:
        saved, failed = save_changed_drafts(args.pad, drafts, before)
        print(f"== pushed {saved} rewritten draft(s) to the pad" + (f", {failed} FAILED" if failed else ""))
        json.dump(store, open(args.store, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        json.dump({"tokens": new_tokens,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "merge_note": "Audit only — these tokens were minted server-side via POST /api/v1/outreach/links; the server store already holds them, no file merge or service restart required."},
                   open(args.out_tokens, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    print(f"== {len(report)} links processed, {len(new_tokens)} new internal tokens minted ==")
    for lead, name, dest, status in report:
        print(f"  [{status:>13}] {lead:12} {name:35} -> {dest}")
    if unresolved:
        print("\n== UNRESOLVED (left on external links — fix before sending) ==")
        for lead, tok, why in unresolved:
            print(f"  {lead:12} {tok[:12]}…  {why}")
    print(f"\nOut: {args.out_tokens}")


if __name__ == "__main__":
    main()