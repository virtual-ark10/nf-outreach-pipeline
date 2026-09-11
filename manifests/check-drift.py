#!/usr/bin/env python3
"""Instance drift check: is this instance still the product, or a fork of it?

Reads manifests/instance-split.json and compares an instance tree against the
pinned product checkout, file by file:

  PRODUCT files  — must be identical. Any difference is an instance-local hack:
                   it belongs upstream as a PR, or in config. Exit code 1 if any.
  ENGINE files   — expected to diverge while two engines coexist; listed every run
                   so the divergence is a decision, not an accident.
  INSTANCE files — config, env, data, docs of this deployment; never compared.

Usage:
  python3 manifests/check-drift.py [--manifest manifests/instance-split.json] [--quiet]

Run it before a deploy and after a pull. It is the check that would have caught
the reverted Dockerfile COPY line that cost two containers.
"""
import argparse
import filecmp
import fnmatch
import json
import pathlib
import subprocess
import sys


def git(repo, *args):
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def matches(rel, patterns):
    for pat in patterns:
        if pat.endswith("/"):
            if rel == pat.rstrip("/") or rel.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(pathlib.Path(rel).name, pat):
            return True
    return False


def tree(root, prefix=""):
    """Every file under root (relative paths), prefix stripped."""
    out = {}
    base = pathlib.Path(root) / prefix if prefix else pathlib.Path(root)
    if not base.exists():
        return out
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(base))
        if ".git/" in rel or rel.startswith(".git"):
            continue
        out[rel] = p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="manifests/instance-split.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    man = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
    prod = pathlib.Path(man["product"]["local_path"])
    inst = pathlib.Path(man["instance_tree"])

    print(f"product : {man['product']['repo']}")
    print(f"          pinned {man['product']['ref']}  local {prod}")
    print(f"instance: {inst}")
    print()

    problems = []
    pinned = man["product"]["ref"]
    if not prod.exists():
        print(f"!! product checkout missing at {prod} — cannot check product files")
    else:
        # The pin is a TAG: compare the checkout against the pinned commit, not against
        # whatever happens to be checked out, or the whole comparison is meaningless.
        head = git(prod, "rev-parse", "--short", "HEAD")
        pinned_sha = git(prod, "rev-parse", "--short", f"{pinned}^{{commit}}")
        if pinned_sha is None:
            print(f"!! the pin {pinned} does not resolve in {prod} — fetch tags first")
            problems.append(f"pin {pinned} unresolvable")
        elif head == pinned_sha:
            print(f"product checkout at {head} = the pin {pinned} ✔")
        else:
            ahead = git(prod, "rev-list", "--count", f"{pinned}..HEAD")
            print(f"!! product checkout is at {head}, NOT the pin {pinned} ({pinned_sha})"
                  + (f" — {ahead} commits ahead" if ahead else ""))
            problems.append(f"product checkout not at {pinned}")
        print()

    prod_files = tree(prod, man["product"].get("root_subdir", ""))
    inst_files = tree(inst, man.get("instance_prefix", ""))

    print("PRODUCT files (must match the pinned product)")
    seen = 0
    for rel in sorted(prod_files):
        if not matches(rel, man["product"]["files"]):
            continue
        seen += 1
        mine = inst_files.get(rel)
        if mine is None:
            if matches(rel, man["product"].get("absent_ok", [])):
                print(f"  absent     {rel}   (declared absent_ok for this instance)")
                continue
            print(f"  MISSING    {rel}   (product has it, this instance does not)")
            problems.append(rel)
            continue
        if filecmp.cmp(prod_files[rel], mine, shallow=False):
            if not args.quiet:
                print(f"  same       {rel}")
        else:
            print(f"  DIFFERS    {rel}   <- instance-local change: upstream it as a PR, or move it into config")
            problems.append(rel)
    print(f"  ({seen} product files compared)")
    print()

    print("ENGINE files (allowed to diverge today — listed so it stays a decision)")
    for rel in sorted(inst_files):
        if not matches(rel, man["engine"]):
            continue
        if rel in prod_files:
            mark = "same     " if filecmp.cmp(prod_files[rel], inst_files[rel], shallow=False) else "DIFFERS  "
            print(f"  {mark}   {rel}")
        else:
            print(f"  instance-only {rel}")
    print()

    print(f"INSTANCE files (config/env/data — never compared): {len(man['instance'])} patterns excluded")
    print()
    if problems:
        print(f"RESULT: {len(problems)} product file(s) diverge — this instance has forked the product there.")
        print("        Either send them upstream as a PR, or record why in the manifest's product.files.")
        return 1
    print("RESULT: no product-file drift. The instance is the pinned product plus its own config and data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
