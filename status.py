#!/usr/bin/env python3
"""One screen: what this project is made of, and whether anything has drifted.

    python3 status.py

Read-only. Answers the four questions that keep getting asked:
  1. Which repo is which, and what is deployed right now?
  2. Has the instance forked the product? (product-file drift)
  3. Are the services alive and what is in the store?
  4. What do I do with a change I just made?

Written because the topology grew (product repo, instance repo, a deploy path, a
second engine) and "which bit am I looking at" stopped being obvious.
"""
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
MANIFEST = ROOT / "manifests" / "instance-split.json"
LIVE = pathlib.Path("/home/boxed/resend-pad")


def sh(*args, cwd=None):
    try:
        out = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=25)
        return out.stdout.strip() if out.returncode == 0 else (out.stderr.strip() or "")
    except Exception as e:
        return f"(failed: {e})"


def http(url):
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.status
    except Exception:
        return None


def main():
    man = json.loads(MANIFEST.read_text())
    prod = pathlib.Path(man["product"]["local_path"])
    pin = man["product"]["ref"]

    print("=" * 78)
    print("PRODUCT  (all the code; the only repo that takes feature PRs)")
    print("=" * 78)
    print(f"  repo      {man['product']['repo']}")
    print(f"  checked   {prod}")
    head = sh("git", "-C", prod, "rev-parse", "--short", "HEAD")
    pin_sha = sh("git", "-C", prod, "rev-parse", "--short", f"{pin}^{{commit}}")
    state = "AT THE PIN ✔" if head == pin_sha else f"!! NOT the pin {pin} ({pin_sha or 'unresolved'})"
    print(f"  pin       {pin} = {pin_sha or 'unresolved'}; checkout at {head} -> {state}")
    tags = [t for t in sh("git", "-C", prod, "tag", "--sort=-creatordate").splitlines() if t][:4]
    print(f"  tags      {', '.join(tags) or '(none)'}")

    print()
    print("=" * 78)
    print("INSTANCE  (config + data + a pin; code changes here are a fork, not a feature)")
    print("=" * 78)
    print(f"  repo      virtual-ark10/nf-outreach-pipeline (this checkout)")
    print(f"  branch    {sh('git', '-C', ROOT, 'rev-parse', '--abbrev-ref', 'HEAD')}"
          f"  head {sh('git', '-C', ROOT, 'rev-parse', '--short', 'HEAD')}")
    dirty = [l for l in sh("git", "-C", ROOT, "status", "--short").splitlines() if l.strip()]
    print(f"  tree      {'clean' if not dirty else str(len(dirty)) + ' uncommitted file(s): ' + ', '.join(l.split()[-1] for l in dirty[:6])}")
    print(f"  deploy    {LIVE}  ->  {os.path.realpath(LIVE)}")
    if LIVE.is_symlink():
        print("            (symlink into this checkout, so deployed bytes = committed bytes)")
    print(f"  live HEAD {sh('git', '-C', str(LIVE), 'log', '--oneline', '-1') if LIVE.exists() else '(missing)'}")

    print()
    print("=" * 78)
    print("DRIFT     (is this instance still the product, or a fork of it?)")
    print("=" * 78)
    if MANIFEST.exists():
        out = subprocess.run([sys.executable, str(ROOT / "manifests" / "check-drift.py"), "--quiet"],
                             capture_output=True, text=True)
        lines = out.stdout.splitlines()
        pin_line = next((l for l in lines if "product checkout" in l), "")
        if pin_line:
            print(f"  {pin_line.strip()}")
        # Only the PRODUCT section counts as drift; the ENGINE section is listed for
        # information (two engines coexisting is a decision, not an accident).
        section = []
        for l in lines:
            if l.startswith("PRODUCT files"):
                section = []
                continue
            if l.startswith("ENGINE files") or l.startswith("INSTANCE files"):
                break
            section.append(l)
        differs = [l.split()[1] for l in section if l.strip().startswith("DIFFERS")]
        missing = [l.split()[1] for l in section if l.strip().startswith("MISSING")]
        absent = len([l for l in section if l.strip().startswith("absent")])
        print(f"  PRODUCT files differing : {len(differs)}  {', '.join(differs[:8]) if differs else '(none)'}")
        print(f"  PRODUCT files missing   : {len(missing)}  {', '.join(missing[:6]) if missing else '(none)'}"
              + (f"  (+{absent} declared absent_ok)" if absent else ""))
        verdict = next((l.strip() for l in lines if l.strip().startswith("RESULT")), "")
        print(f"  {verdict or '(no verdict)'}")
        if differs:
            print("  -> each of those is an instance-local change that belongs upstream as a PR,")
            print("     or in config. That is the list to shrink; it is the fork.")
    print(f"  full report: python3 manifests/check-drift.py")

    print()
    print("=" * 78)
    print("SERVICES  (are they alive, and what is in the store?)")
    print("=" * 78)
    print(f"  pad  :3001 /api/health -> {http('http://127.0.0.1:3001/api/health')}")
    print(f"  crm  :3002 /api/health -> {http('http://127.0.0.1:3002/api/health')}")
    dbp = LIVE / "data" / "outreach.db"
    if dbp.exists():
        try:
            c = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            q = lambda s: c.execute(s).fetchone()[0]
            print(f"  store     leads {q('select count(*) from leads')}"
                  f" | emails {q('select count(*) from emails')}"
                  f" | replies {q('select count(*) from replies')}"
                  f" | events {q('select count(*) from events')}"
                  f" | engagements {q('select count(*) from email_engagements')}")
            cols = [r[1] for r in c.execute("PRAGMA table_info(events)")]
            pending = q("select count(*) from events where processed_at is null") if "processed_at" in cols else "n/a"
            print(f"  queue     pending events {pending} (drained on every request)")
        except Exception as e:
            print(f"  store     (could not read: {e})")

    print()
    print("=" * 78)
    print("WORKFLOW  (the only two ways a change ships)")
    print("=" * 78)
    print("  Code change (server.cjs, index.html, an engine file, docs):")
    print("     git checkout -b <name>   ->   edit   ->   bash tools/run-sqlite-e2e.sh")
    print("     git fetch origin && git rebase origin/main   ->   git push   ->   PR")
    print("     shared files (server.cjs, index.html, config.example.json, Dockerfile,")
    print("     docker-compose.yml, docs) go through a PR; single-owner files may go direct.")
    print("  Config change (config.json, .env, data/, a new tenant):")
    print("     edit and restart. No branch, no PR — it is not code.")
    print("  Then: refresh the pin/archive deliberately.")
    print("     node tools/db-snapshot.cjs attic/<name>.db      # archive point for the store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
