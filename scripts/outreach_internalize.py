#!/usr/bin/env python3
"""Internalize outreach links — newsletters must never send valuable leads to external sites.

Sweeps the Pad draft queue (drafts.json), and for every outreach link whose stored
destination is an EXTERNAL publication site (substack.com, thedigitalcreator.co, ...),
Resolves the publication to its NewsletterFIT internal page via the corpus search API,
mints a NEW token via POST /api/v1/outreach/links (server-side — no file merge or
API restart needed), and rewrites the draft (text + html) to use the internal token.

Policy: all outreach links are internal. The lead stays on newsletterfit.com and the
nf_attr cookie + click counts still attribute the visit — no traffic is given away.

Outputs:
  - drafts.json rewritten (internal tokens)
  - attribution store (home) mirrored with the minted tokens (dedupe cache)
  - attribution.new-tokens.json — audit log of tokens minted this run (server already
    holds them; no merge/restart required)
  - a report line per swap; unresolved pubs are reported and left untouched

Usage:
  python3 outreach_internalize.py [--drafts path] [--store path] [--dry-run]
Env (or --flag): NEWSLETTERFIT_API, API_BEARER_TOKEN, NF_BASE_URL
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
    ap.add_argument("--drafts", default="/home/boxed/resend-pad/data/drafts.json")
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

    drafts = json.load(open(args.drafts, encoding="utf-8"))
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
        json.dump(drafts, open(args.drafts, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
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