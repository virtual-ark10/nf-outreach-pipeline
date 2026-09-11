# Product / engine / instance — the split, and the check that keeps it honest

One repo playing three roles is what makes branches and PRs collide: the same files
carry product change and instance change at once. This directory holds the split, so
the collision is something you can measure instead of argue about.

    python3 manifests/check-drift.py

## The three classes

- **PRODUCT** — the brand-agnostic pad kit (`virtual-ark10/resend-pad`, cloned at
  `/home/boxed/pad-kit`). This is the only place feature branches and PRs belong, and
  the only place two agents legitimately collide. A PRODUCT file inside an instance
  tree must be byte-identical to the pinned product commit.
- **ENGINE** — the store and the pipeline loop (`db.cjs`, `schema.sql`,
  `pipeline.cjs`, `hooks.cjs`, `leads/`, `tools/`, `tests/`). Owned by whoever owns
  the live data. Two engines exist today (this instance's SQLite store and the kit's
  `db.cjs` + `hooks.cjs`), so divergence is EXPECTED — which is exactly why the check
  prints it every run. It is a decision, not an accident.
- **INSTANCE** — config, env, data and logs. Never compared. A tenant is a
  directory, not a branch: its own `config.json`, `.env`, `data/`, service name,
  deploy dir, proxy snippet and tracking subdomain.

## What the check reports

- `same` / `DIFFERS` for every PRODUCT file present in both trees. **DIFFERS is a
  hack that has to go upstream as a PR, or move into config.** Exit code 1 if any.
- `absent` for product files declared `absent_ok` (this instance deliberately does
  not carry the container bits).
- The ENGINE divergence, listed every run.
- Whether the product checkout is behind `origin/main` — a stale pin makes the whole
  comparison meaningless, so fetch first.

## Changing the pin

Edit `product.ref` in `instance-split.json` after fetching the product. Do not edit
product files in the instance tree to "fix" a diff; that is the fork this exists to
prevent.

## Where the instance lives

`/home/boxed/resend-pad` is a **symlink to `/home/boxed/nf-outreach-pipeline/pad`**,
so the deployed bytes and the committed bytes are the same files by construction.
Before 2026-09-11 that path was a clone of a foreign repo (`rozetyp/resend-email`)
with this instance's code untracked inside it; a deploy from that remote could have
silently reverted a product file. The old directory is kept for rollback as
`/home/boxed/resend-pad.pre-symlink-*`, and the foreign `.git` from the live tree is
parked in `pad/attic/`.

Config, state and logs stay local and out of git: `.env`, `data/` (except the
archived `outreach.db`), `attic/`, `*.log`. See `pad/.gitignore`.
