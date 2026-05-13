# Kaizen Rename — Design Spec

**Date:** 2026-05-13
**Status:** Approved (brainstorm), pending implementation plan
**Author:** Mason Misch

## Goal

Rename the project from **MiniClaw** to **Kaizen** everywhere it appears:
code, docs, configs, filesystem paths, GitHub repo, environment
variables, and the MemPalace wing. No legacy shims, no parallel naming
— a full, total cutover.

## Scope (locked decisions)

| Dimension | Decision |
|---|---|
| Rename breadth | Everything (code + repo + config dir + env vars + wing + docs + CLI) |
| GitHub repo | Rename in place via `gh repo rename` (preserves stars/issues/redirects) |
| User data at `~/.miniclaw/` | Move to `~/.kaizen/` (single cutover, no copy) |
| MemPalace wing | Rename `wing_miniclaw` → `wing_kaizen` (UPDATE in place) |
| Env var prefix | `KAIZEN_*` (direct replacement of `MINICLAW_*`) |
| Sibling worktree `~/linux/miniclaw-voice-pipeline` | Rename dir, repair worktree pointer |
| Historical plan/spec docs | Option A — rewrite all of them too (full consistency) |

## Out of scope

- Adding migration shims, legacy-name fallbacks, or compatibility layers.
- Refactoring code that is touched only because of a name change.
- Deleting the backup tarball — left in place for a few days post-cutover.
- Pre-existing dead code or unrelated cleanup.

## Survey baseline (2026-05-13)

- ~1,000 textual hits across three case forms (MiniClaw 223, miniclaw 737, MINICLAW 45)
- 4 paths/filenames with `miniclaw` to `git mv`:
  - `config/systemd/miniclaw.service`
  - `docs/superpowers/specs/2026-04-10-miniclaw-dashboard-design.md`
  - `docs/superpowers/plans/2026-05-04-miniclaw-voice-pipeline.md`
  - `docs/superpowers/specs/2026-05-04-miniclaw-voice-pipeline-design.md`
- ~30 living source files under `core/` referencing the name
- ~50 historical plan/spec docs under `docs/superpowers/` referencing it
- Worktree at `~/linux/miniclaw-voice-pipeline` shares origin with `~/linux/miniclaw`

## Phases

Each phase is independently reversible until the backup tarball is deleted (see Rollback).

### Phase 1 — Pre-flight

1. Confirm a clean working tree in `~/linux/miniclaw` and the worktree at `~/linux/miniclaw-voice-pipeline` (commit or stash anything in flight).
2. Snapshot: `tar -czf ~/.miniclaw.backup.2026-05-13.tgz ~/.miniclaw` and `git tag rename/pre-kaizen` in both checkouts. Push the tag.
3. Stop any running MiniClaw process / systemd unit (`systemctl --user stop miniclaw` if loaded).

### Phase 2 — Code & content rewrite

Branch: `rename/kaizen` off `main`.

Three case-preserving sed passes over tracked files (excludes `.git`, `__pycache__`, archives):

- `MiniClaw` → `Kaizen`
- `miniclaw` → `kaizen`
- `MINICLAW` → `KAIZEN`

Driven by `scripts/rename_to_kaizen.sh` using `git ls-files` + `sed -i`, committed in the same commit so the move is auditable and re-runnable.

`git mv` for the four paths listed in the survey.

Historical plan/spec docs are rewritten too (Option A). The script's own literal `miniclaw` strings are also rewritten so `git grep -i miniclaw` returns zero hits inside tracked files post-commit.

### Phase 3 — Local commit & verification

On `rename/kaizen`:

1. Run the rename script; review `git diff --stat`; spot-check `core/orchestrator.py`, `CLAUDE.md`, `.env.example`.
2. Run `pytest` against the renamed code, *before* any filesystem moves.
3. Run `git grep -i miniclaw` — must return zero.
4. Commit: `rename: miniclaw → kaizen across code, docs, configs`.

### Phase 4 — Filesystem & data moves

Order: code merged first, data second, so a half-done state is rollback-clean.

1. Merge `rename/kaizen` into `main` locally (fast-forward). Do not push yet.
2. `mv ~/.miniclaw ~/.kaizen`. Verify `~/.kaizen/memory/` and `sqlite3 ~/.kaizen/sessions.db '.tables'`.
3. Rename worktree dir, then main checkout, then repair the worktree pointer:
   - `mv ~/linux/miniclaw-voice-pipeline ~/linux/kaizen-voice-pipeline`
   - `mv ~/linux/miniclaw ~/linux/kaizen`
   - `cd ~/linux/kaizen && git worktree repair ~/linux/kaizen-voice-pipeline`
4. Smoke: `cd ~/linux/kaizen && python main.py --skill-select "what's the weather"`. Expect a clean skill-select result with zero `miniclaw` in logs.

### Phase 5 — GitHub rename + remotes

1. `gh repo rename kaizen --repo M8SON/miniclaw`.
2. In both checkouts: `git remote set-url origin https://github.com/M8SON/kaizen.git`.
3. `git push origin main`. Push the `rename/pre-kaizen` tag if not yet pushed.
4. Wait for CI on the renamed repo to go green. Fix any workflow that hardcoded the repo name.

### Phase 6 — MemPalace wing rename

1. Record the pre-migration drawer count: `mempalace_list_drawers wing=wing_miniclaw` (expected 32 per the 2026-05-10 baseline). Capture the exact number for the verification gate.
2. `grep -r wing_miniclaw ~/linux/nexus` — capture the file list (wake-up wiring, SessionStart hook config).
3. Inspect MemPalace schema (`.schema` on the SQLite store) to find every table carrying a wing FK (drawers, rooms, tunnels, …). Run `UPDATE … SET wing = 'wing_kaizen' WHERE wing = 'wing_miniclaw'` against each.
4. Update nexus wiring: replace the `~/linux/miniclaw` → `wing_miniclaw` mapping with `~/linux/kaizen` → `wing_kaizen`.
5. Verify:
   - `mempalace_search` scoped to `wing_kaizen` for "MiniClaw project overview" returns the durable-facts drawer.
   - `mempalace_search` scoped to `wing_miniclaw` returns zero.
   - `mempalace_list_drawers wing=wing_kaizen` returns the count captured in step 1.
6. Editorial pass on drawer *text* (not just wing column): update durable drawers whose text still references "MiniClaw" or `~/.miniclaw/` paths. Use `mempalace_update_drawer`. Start with `project_miniclaw.md` (rename + update path references).

### Phase 7 — Wrap-up & verification

1. Update Nexus-side references: `/home/daedalus/linux/CLAUDE.md` and `nexus/nexus/policies/*` if they reference `wing_miniclaw` or `~/linux/miniclaw`.
2. Final cross-workspace grep: `grep -r -i miniclaw ~/linux --exclude-dir=.git --exclude-dir=__pycache__`. Expected residue: the backup tarball, the `rename/pre-kaizen` tag refs, the `_archive_2026-05-10/` frozen archive. No live references.
3. End-to-end smoke: launch the assistant normally; issue a command that exercises memory; confirm session row in `~/.kaizen/sessions.db`, write to `~/.kaizen/memory/`, and (on the next 15-message save tick) a save into `wing_kaizen`.
4. Leave `~/.miniclaw.backup.2026-05-13.tgz` in place for a few days. Delete in a follow-up after confidence is established.
5. Save a project memory to `wing_kaizen` summarizing the rename and the date.

## Verification gates

| Gate | When | Pass condition |
|---|---|---|
| Tests on renamed code | End of Phase 3 | `pytest` exit 0 |
| Zero `miniclaw` in tracked files | End of Phase 3 | `git grep -i miniclaw` returns nothing |
| Local smoke | End of Phase 4 | `--skill-select` runs cleanly against `~/.kaizen/` |
| CI on renamed repo | End of Phase 5 | GitHub Actions green on `main` |
| MemPalace migration | End of Phase 6 | drawer count under `wing_kaizen` matches the count recorded in Phase 6 step 1; `wing_miniclaw` returns zero hits |
| End-to-end smoke | End of Phase 7 | Memory + sessions writes land under `~/.kaizen/`; new MemPalace save lands under `wing_kaizen` |

## Rollback

Phase-by-phase reversibility while the backup tarball exists:

- **After Phase 3:** `git reset --hard rename/pre-kaizen` on `main`.
- **After Phase 4:** as above, plus `mv ~/.kaizen ~/.miniclaw`, `mv ~/linux/kaizen-voice-pipeline ~/linux/miniclaw-voice-pipeline`, `mv ~/linux/kaizen ~/linux/miniclaw`, `git worktree repair`.
- **After Phase 5:** `gh repo rename miniclaw --repo M8SON/kaizen` reverses the GitHub rename. Reset remote URLs.
- **After Phase 6:** inverse `UPDATE drawers SET wing = 'wing_miniclaw' WHERE wing = 'wing_kaizen'` (and sibling tables).

Once the backup tarball is deleted, rollback of user data is no longer possible.

## Risks & mitigations

- **Worktree pointer breakage when parent dir is renamed.** Mitigated by `git worktree repair` after both `mv`s.
- **MemPalace schema has wing FK in tables we forgot.** Mitigated by reading `.schema` first and UPDATE-ing every wing column.
- **Historical doc rewrite mutates dated snapshots.** Accepted (Option A) for total consistency.
- **GitHub redirect doesn't cover every consumer.** Local remote URLs updated explicitly; any external bookmarks rely on GitHub's redirect.
- **The rename script's literal strings.** Rewritten in the same pass so `git grep` is clean post-commit.
