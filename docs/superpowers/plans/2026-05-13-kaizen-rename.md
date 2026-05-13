# Kaizen Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename MiniClaw → Kaizen across code, filesystem, GitHub repo, MemPalace wing, and Nexus wiring as a single total cutover.

**Architecture:** Seven sequential phases, each independently reversible while a pre-cutover snapshot exists. Mechanical text rewrites are driven by a committed, auditable script; filesystem and external-system changes are explicit one-shot commands. No legacy shims, no parallel naming.

**Tech Stack:** bash + sed + git for code rewrite; `gh` CLI for GitHub repo rename; sqlite3 for MemPalace wing migration; existing pytest suite for verification.

**Spec:** `docs/superpowers/specs/2026-05-13-kaizen-rename-design.md`

---

## Files created / modified

- **Create:** `scripts/rename_to_kaizen.sh` (committed alongside the rewrite)
- **Modify (via script):** every tracked file containing `miniclaw` / `MiniClaw` / `MINICLAW` — ~30 source files under `core/`, plus `CLAUDE.md`, `README.md`, `WORKING_MEMORY.md`, `.env.example`, `conftest.py`, `main.py`, `run.sh`, and ~50 historical plan/spec docs.
- **Rename via `git mv`:** `config/systemd/miniclaw.service` and three dated docs under `docs/superpowers/specs|plans/`.
- **Filesystem renames:** `~/.miniclaw` → `~/.kaizen`; `~/linux/miniclaw[-voice-pipeline]` → `~/linux/kaizen[-voice-pipeline]`.
- **External:** GitHub repo `M8SON/miniclaw` → `M8SON/kaizen`; MemPalace wing column `wing_miniclaw` → `wing_kaizen`; nexus wiring under `~/linux/nexus`.

---

## Phase 1 — Pre-flight

### Task 1: Snapshot and stop services

**Files:** none modified; safety setup only.

- [ ] **Step 1: Verify clean working trees in both checkouts**

```bash
cd ~/linux/miniclaw && git status --short
cd ~/linux/miniclaw-voice-pipeline && git status --short
```

Expected: both commands print nothing. If either is dirty, commit or stash before proceeding.

- [ ] **Step 2: Tag the pre-rename state and push**

```bash
cd ~/linux/miniclaw
git tag rename/pre-kaizen
git push origin rename/pre-kaizen
```

Expected: `* [new tag]         rename/pre-kaizen -> rename/pre-kaizen`.

- [ ] **Step 3: Snapshot user data**

```bash
tar -czf ~/.miniclaw.backup.2026-05-13.tgz -C ~ .miniclaw
ls -lh ~/.miniclaw.backup.2026-05-13.tgz
```

Expected: a tarball whose size is roughly that of `du -sh ~/.miniclaw`.

- [ ] **Step 4: Stop any running instance**

```bash
systemctl --user stop miniclaw 2>/dev/null || true
pgrep -af 'main.py' || echo "no main.py running"
```

Expected: no `main.py` process listed.

---

## Phase 2 — Rewrite code and content

### Task 2: Create branch and write the rename script

**Files:**
- Create: `~/linux/miniclaw/scripts/rename_to_kaizen.sh`

- [ ] **Step 1: Create the branch**

```bash
cd ~/linux/miniclaw
git checkout -b rename/kaizen
```

Expected: `Switched to a new branch 'rename/kaizen'`.

- [ ] **Step 2: Write `scripts/rename_to_kaizen.sh`**

The patterns are constructed via `printf` so the script source itself contains no literal `miniclaw` / `MiniClaw` / `MINICLAW`. This means `git grep -i miniclaw` returns zero hits post-rewrite without any special-casing.

```bash
#!/usr/bin/env bash
# One-shot rename: miniclaw -> kaizen across all tracked files.
# Patterns are built via printf so this script's source contains no
# literal occurrence of the legacy name.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

LC=$(printf 'mini%s' 'claw')
PC=$(printf 'Mini%s' 'Claw')
UC=$(printf 'MINI%s' 'CLAW')

mapfile -t FILES < <(git ls-files)

changed=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  if file --mime "$f" 2>/dev/null | grep -q 'charset=binary'; then
    continue
  fi
  if grep -qE "$PC|$LC|$UC" "$f"; then
    sed -i \
      -e "s/$PC/Kaizen/g" \
      -e "s/$LC/kaizen/g" \
      -e "s/$UC/KAIZEN/g" \
      "$f"
    changed=$((changed + 1))
  fi
done

echo "Rewrote ${changed} file(s)."
```

- [ ] **Step 3: Make it executable and stage it**

```bash
chmod +x scripts/rename_to_kaizen.sh
git add scripts/rename_to_kaizen.sh
```

Expected: no errors.

- [ ] **Step 4: Confirm the script source contains no legacy literal**

```bash
grep -ciE 'miniclaw' scripts/rename_to_kaizen.sh
```

Expected: `0`.

---

### Task 3: `git mv` paths with `miniclaw` in their names

**Files:** four file renames.

- [ ] **Step 1: Rename the four paths**

```bash
cd ~/linux/miniclaw
git mv config/systemd/miniclaw.service config/systemd/kaizen.service
git mv docs/superpowers/specs/2026-04-10-miniclaw-dashboard-design.md \
       docs/superpowers/specs/2026-04-10-kaizen-dashboard-design.md
git mv docs/superpowers/plans/2026-05-04-miniclaw-voice-pipeline.md \
       docs/superpowers/plans/2026-05-04-kaizen-voice-pipeline.md
git mv docs/superpowers/specs/2026-05-04-miniclaw-voice-pipeline-design.md \
       docs/superpowers/specs/2026-05-04-kaizen-voice-pipeline-design.md
```

- [ ] **Step 2: Verify no other paths still contain the legacy name**

```bash
git ls-files | grep -i miniclaw
```

Expected: nothing printed.

---

### Task 4: Run the rename script and verify zero residual hits

**Files:** ~30 source files + ~50 docs rewritten in place.

- [ ] **Step 1: Run the script**

```bash
cd ~/linux/miniclaw
./scripts/rename_to_kaizen.sh
```

Expected output: a single line `Rewrote NNN file(s).` with NNN ≥ 80.

- [ ] **Step 2: Verify no tracked file contains the legacy name**

```bash
git grep -i miniclaw
```

Expected: no output, exit code 1 (grep "no matches").

- [ ] **Step 3: Spot-check critical files**

```bash
grep -nE 'Kaizen|kaizen|KAIZEN' CLAUDE.md | head
grep -nE 'Kaizen|kaizen|KAIZEN' core/orchestrator.py | head
grep -nE 'KAIZEN_' .env.example | head
```

Expected: all three show plausible hits (no `miniclaw` in any form).

- [ ] **Step 4: Stat the diff**

```bash
git diff --stat | tail -5
```

Expected: a line like `~80 files changed, NNNN insertions(+), NNNN deletions(-)`.

---

## Phase 3 — Local verification and commit

### Task 5: Run the test suite against the renamed code

**Files:** none changed; verification only.

- [ ] **Step 1: Run pytest**

```bash
cd ~/linux/miniclaw
pytest -q 2>&1 | tail -20
```

Expected: all tests pass. If a test fails, do not commit — read the failure, identify the cause (likely a string that should not have been rewritten, e.g. a URL or external API token), fix it manually, re-run, then continue.

- [ ] **Step 2: Stage all changes**

```bash
git add -A
git status --short | head -20
```

Expected: every change appears as `M` (modified) or `R` (renamed). No untracked files except the rename script (already staged).

- [ ] **Step 3: Commit**

```bash
git -c commit.gpgsign=false commit -m "$(cat <<'EOF'
rename: miniclaw → kaizen across code, docs, configs

Single atomic rename driven by scripts/rename_to_kaizen.sh, which
walks git ls-files and runs three case-preserving sed passes. Also
git mv's four paths whose filenames contained the legacy name. No
legacy shims or fallbacks per the rename spec.

Spec: docs/superpowers/specs/2026-05-13-kaizen-rename-design.md
EOF
)"
```

Expected: a single commit on `rename/kaizen` with ~80 files changed.

- [ ] **Step 4: Merge to local main (no push yet)**

```bash
git checkout main
git merge --ff-only rename/kaizen
```

Expected: fast-forward succeeds.

---

## Phase 4 — Filesystem and data moves

### Task 6: Move user data

**Files:** filesystem moves only.

- [ ] **Step 1: Move the user data directory**

```bash
mv ~/.miniclaw ~/.kaizen
```

Expected: no output.

- [ ] **Step 2: Verify memory vault and sessions DB are intact**

```bash
ls ~/.kaizen/memory | head
sqlite3 ~/.kaizen/sessions.db '.tables'
```

Expected: memory directory listing matches what was there before; `.tables` lists the sessions schema (e.g. `sessions`, `sessions_fts`, FTS5 shadow tables).

---

### Task 7: Rename the worktree and main checkout, repair worktree pointer

**Files:** filesystem moves only.

- [ ] **Step 1: Rename worktree dir first, then the main checkout**

```bash
mv ~/linux/miniclaw-voice-pipeline ~/linux/kaizen-voice-pipeline
mv ~/linux/miniclaw ~/linux/kaizen
```

Expected: no output. The worktree's `.git` file currently points to a now-missing path inside `~/linux/miniclaw/.git/worktrees/...`, which is fine because we repair next.

- [ ] **Step 2: Repair the worktree pointer**

```bash
cd ~/linux/kaizen
git worktree repair ~/linux/kaizen-voice-pipeline
```

Expected: `Repairing ...` line, no errors.

- [ ] **Step 3: Verify both checkouts work**

```bash
cd ~/linux/kaizen && git status --short
cd ~/linux/kaizen-voice-pipeline && git status --short
```

Expected: both report a clean tree on their respective branches (main vs whatever the worktree was on).

---

### Task 8: Local smoke test

**Files:** none changed; verification only.

- [ ] **Step 1: Run skill selection without an API call**

```bash
cd ~/linux/kaizen
python main.py --skill-select "what's the weather"
```

Expected: a ranked-skill output (a list with similarity scores). No tracebacks. No log lines mentioning `miniclaw` or `~/.miniclaw/`.

- [ ] **Step 2: Inspect logs for stale references**

```bash
journalctl --user -u kaizen --since "5 min ago" 2>/dev/null | grep -i miniclaw || echo "no stale refs"
```

Expected: `no stale refs`.

---

## Phase 5 — GitHub rename and remotes

### Task 9: Rename the GitHub repo and update remotes

**Files:** git remote URLs in two checkouts.

- [ ] **Step 1: Rename on GitHub**

```bash
gh repo rename kaizen --repo M8SON/miniclaw
```

Expected: confirmation that `M8SON/miniclaw` is now `M8SON/kaizen`. GitHub auto-redirects old URLs; stars/issues/PRs preserved.

- [ ] **Step 2: Update local remote URLs**

```bash
cd ~/linux/kaizen && git remote set-url origin https://github.com/M8SON/kaizen.git
cd ~/linux/kaizen-voice-pipeline && git remote set-url origin https://github.com/M8SON/kaizen.git
```

- [ ] **Step 3: Verify remotes**

```bash
cd ~/linux/kaizen && git remote -v
cd ~/linux/kaizen-voice-pipeline && git remote -v
```

Expected: all four lines show `https://github.com/M8SON/kaizen.git`.

- [ ] **Step 4: Push main**

```bash
cd ~/linux/kaizen
git push origin main
```

Expected: fast-forward push of the rename commit. If the push is rejected (someone pushed concurrently), pull rebase and try again.

- [ ] **Step 5: Wait for CI**

```bash
gh run watch --repo M8SON/kaizen
```

Expected: workflow completes green. If anything in `.github/workflows/` hardcoded the repo name (it shouldn't — the rename script rewrote it), fix and re-push.

---

## Phase 6 — MemPalace wing migration

### Task 10: Record baseline and locate the wing column

**Files:** none changed; inspection only.

- [ ] **Step 1: Record drawer counts before migration**

```bash
# Via MCP from the assistant: mempalace_list_drawers wing=wing_miniclaw
# Note the total count.
```

Expected: count is `32` per the 2026-05-10 baseline. Record the exact number — verification in Task 13 will compare against it.

- [ ] **Step 2: Locate the SQLite store(s) holding the wing column**

```bash
ls -la ~/.mempalace/
for db in ~/.mempalace/*.sqlite3 ~/.mempalace/palace/*.sqlite3; do
  echo "=== $db ==="
  sqlite3 "$db" ".schema" 2>/dev/null | grep -i wing
done
```

Expected: at least one table (likely `drawers` in `knowledge_graph.sqlite3` or chroma's metadata table in `palace/chroma.sqlite3`) has a column or value of type wing. Record every `(db_path, table, column)` triple you find — Task 11 updates all of them.

- [ ] **Step 3: Find Nexus wiring that maps repo paths to wings**

```bash
grep -rn 'wing_miniclaw\|/linux/miniclaw' ~/linux/nexus --include='*.py' --include='*.toml' --include='*.yaml' --include='*.yml' --include='*.json' --include='*.md'
```

Expected: a short list of files (likely under `nexus/nexus/`) that need both `wing_miniclaw` → `wing_kaizen` and `/linux/miniclaw` → `/linux/kaizen` rewrites.

---

### Task 11: Migrate the wing column

**Files:** SQLite UPDATEs against the stores identified in Task 10.

- [ ] **Step 1: Back up the MemPalace stores**

```bash
cp ~/.mempalace/knowledge_graph.sqlite3 ~/.mempalace/knowledge_graph.sqlite3.pre-kaizen
cp ~/.mempalace/palace/chroma.sqlite3 ~/.mempalace/palace/chroma.sqlite3.pre-kaizen
```

Expected: no output.

- [ ] **Step 2: Run UPDATE on every `(db, table, column)` triple from Task 10 step 2**

For each triple, run the equivalent of:

```bash
sqlite3 <DB_PATH> "UPDATE <TABLE> SET <COL> = 'wing_kaizen' WHERE <COL> = 'wing_miniclaw';"
sqlite3 <DB_PATH> "SELECT changes();"
```

Expected: `changes()` returns a positive number on at least one of the tables. Sum should be ≥ 32 (one row per drawer) plus any rooms/tunnels rows. Note the per-table counts.

- [ ] **Step 3: Confirm zero rows remain on the old wing**

For each triple:

```bash
sqlite3 <DB_PATH> "SELECT COUNT(*) FROM <TABLE> WHERE <COL> = 'wing_miniclaw';"
```

Expected: `0` for every triple.

---

### Task 12: Update Nexus wiring to point at the new wing

**Files:** the file list captured in Task 10 step 3.

- [ ] **Step 1: Rewrite each file from Task 10 step 3**

For each file, apply both substitutions:
- `wing_miniclaw` → `wing_kaizen`
- `/linux/miniclaw` (and any other path stems pointing at the old location) → `/linux/kaizen`

Use `sed -i` per file. After each, confirm:

```bash
grep -c 'miniclaw' <FILE>
```

Expected: `0`.

- [ ] **Step 2: Confirm no stale references remain in `~/linux/nexus`**

```bash
grep -rn 'miniclaw' ~/linux/nexus --include='*.py' --include='*.toml' --include='*.yaml' --include='*.yml' --include='*.json' --include='*.md'
```

Expected: no output.

- [ ] **Step 3: Commit nexus changes**

```bash
cd ~/linux/nexus
git add -A
git status --short
git -c commit.gpgsign=false commit -m "rename: point at ~/linux/kaizen and wing_kaizen"
```

Expected: a small commit listing only the wiring files you touched.

---

### Task 13: Verify the MemPalace migration

**Files:** none changed; verification only.

- [ ] **Step 1: Confirm new wing returns the recorded count**

```bash
# Via MCP: mempalace_list_drawers wing=wing_kaizen
```

Expected: count matches the number recorded in Task 10 step 1.

- [ ] **Step 2: Confirm new wing returns useful search results**

```bash
# Via MCP: mempalace_search query="MiniClaw project overview" wing=wing_kaizen
```

Expected: at least one drawer comes back. (The text inside drawers may still say "MiniClaw" — that's the editorial pass in Task 14.)

- [ ] **Step 3: Confirm old wing is empty**

```bash
# Via MCP: mempalace_search query="MiniClaw project overview" wing=wing_miniclaw
```

Expected: zero results.

---

### Task 14: Editorial pass on drawer text

**Files:** durable drawers under `wing_kaizen` whose text still says "MiniClaw" or `~/.miniclaw/`.

This is content rewriting, not just a column change. The wing FK is mechanical; the *narrative* of each memory needs human-readable updates.

- [ ] **Step 1: List durable project/user drawers**

```bash
# Via MCP: mempalace_list_drawers wing=wing_kaizen
# Look for type=project, type=user, type=feedback drawers whose text
# references "MiniClaw", "miniclaw", or "~/.miniclaw/".
```

Record a list of drawer IDs that need text updates. Expected candidates:
- `project_miniclaw.md` (path: `~/linux/miniclaw` → `~/linux/kaizen`; name; data-dir path)
- `user_mason.md` (passing reference to MiniClaw as the project name)
- Activity-snapshot drawers dated 2026-04-07 and 2026-04-11 (these are dated historical snapshots — update only the *current-state* fields, not the historical-state ones)
- `feedback_*` drawers that mention MiniClaw in their **Why:** lines — update only if the reference is to the *current* project, not to a dated incident

- [ ] **Step 2: Update each drawer**

For each drawer ID in the list:

```bash
# Via MCP: mempalace_update_drawer drawer_id=<ID> text=<NEW_TEXT>
```

When rewriting:
- Replace project name `MiniClaw` → `Kaizen`.
- Replace paths `~/linux/miniclaw` → `~/linux/kaizen`, `~/.miniclaw/` → `~/.kaizen/`.
- Replace the GitHub remote where mentioned.
- Leave historical/dated content (e.g. "as of 2026-04-22") intact — those are time-stamped snapshots.

Expected: drawer text returns clean from `mempalace_search` with no stale references.

- [ ] **Step 3: Confirm cleanup**

```bash
# Via MCP: mempalace_search query="miniclaw" wing=wing_kaizen max_distance=1.5
```

Expected: any remaining hits are inside historical/dated snapshots that were intentionally preserved (verify each by reading; if any current-state drawer still says "miniclaw", update it).

---

## Phase 7 — Wrap-up and final verification

### Task 15: Update Nexus-side root CLAUDE.md and policies

**Files:**
- Modify: `/home/daedalus/linux/CLAUDE.md`
- Modify: any file under `~/linux/nexus/nexus/policies/` that references `wing_miniclaw` or `~/linux/miniclaw`

(Task 12 already swept `~/linux/nexus` — this task picks up any sibling files like the root `CLAUDE.md` that aren't inside the nexus package.)

- [ ] **Step 1: Grep**

```bash
grep -rn 'miniclaw\|MiniClaw\|MINICLAW' /home/daedalus/linux/CLAUDE.md ~/linux/nexus/nexus/policies/
```

Expected: a short list, possibly empty.

- [ ] **Step 2: Rewrite using the same three substitutions**

For each file with hits, run:

```bash
sed -i -e 's/MiniClaw/Kaizen/g' -e 's/miniclaw/kaizen/g' -e 's/MINICLAW/KAIZEN/g' <FILE>
```

- [ ] **Step 3: Verify**

```bash
grep -rn 'miniclaw\|MiniClaw\|MINICLAW' /home/daedalus/linux/CLAUDE.md ~/linux/nexus/nexus/policies/
```

Expected: no output.

- [ ] **Step 4: Commit if any nexus files changed**

```bash
cd ~/linux/nexus
git status --short
# If anything is modified:
git add -A && git -c commit.gpgsign=false commit -m "rename: policies and root CLAUDE.md → kaizen"
```

---

### Task 16: Final cross-workspace grep

**Files:** none changed; verification only.

- [ ] **Step 1: Sweep**

```bash
grep -rl --binary-files=without-match -i 'miniclaw' ~/linux 2>/dev/null \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=node_modules \
  --exclude-dir=.pytest_cache --exclude-dir=.venv
```

Expected residue (acceptable):
- `~/linux` may contain no `~/.miniclaw.backup.2026-05-13.tgz` — that's at `~/`, not `~/linux`, so it shouldn't appear.
- `_archive_2026-05-10/` directory under `~/.claude/projects/-home-daedalus-linux/memory/` may contain frozen archive content — intentional per the retirement note.

Anything else: investigate and rewrite.

- [ ] **Step 2: Sweep `~/` for the backup tarball reference only**

```bash
ls -la ~/.miniclaw.backup.2026-05-13.tgz
```

Expected: file exists. Leave it in place — Task 19 covers eventual cleanup.

---

### Task 17: End-to-end smoke test

**Files:** none changed; verification only.

- [ ] **Step 1: Start the assistant normally**

```bash
cd ~/linux/kaizen
./run.sh
```

(Run interactively in a separate terminal if `run.sh` blocks. The remaining steps assume it's running.)

Expected: clean startup, no tracebacks. Skill index loads. No log lines mentioning `miniclaw` or `~/.miniclaw/`.

- [ ] **Step 2: Issue a command that touches memory**

Send the assistant a message that exercises `save_memory` (e.g. "remember that the project was renamed today"). Then:

```bash
sqlite3 ~/.kaizen/sessions.db "SELECT COUNT(*) FROM sessions WHERE created_at > datetime('now', '-5 minutes');"
ls -lt ~/.kaizen/memory | head -5
```

Expected: session count ≥ 1; a fresh file appears in `~/.kaizen/memory`.

- [ ] **Step 3: Verify the next MemPalace save lands on the new wing**

After the 15-message threshold trips (or trigger PreCompact manually if your config allows), inspect the most recent drawer:

```bash
# Via MCP: mempalace_list_drawers wing=wing_kaizen limit=5 (sorted by created_at desc)
```

Expected: a freshly-created drawer with `wing=wing_kaizen` and `created_at` after the smoke-test moment.

- [ ] **Step 4: Stop the assistant cleanly**

```bash
systemctl --user stop kaizen 2>/dev/null || pkill -f 'main.py'
```

---

### Task 18: Save the rename to memory and update the spec

**Files:** none directly; MemPalace write only.

- [ ] **Step 1: Save a project memory under `wing_kaizen`**

```bash
# Via MCP: mempalace_kg_add or mempalace_add_drawer
# wing: wing_kaizen
# type: project
# name: "MiniClaw → Kaizen rename, 2026-05-13"
# text:
#   Renamed MiniClaw → Kaizen on 2026-05-13.
#   - Local: ~/linux/miniclaw → ~/linux/kaizen (worktree: ~/linux/kaizen-voice-pipeline)
#   - User data: ~/.miniclaw → ~/.kaizen (memory vault + sessions.db preserved)
#   - GitHub: M8SON/miniclaw → M8SON/kaizen (gh repo rename; redirects in place)
#   - MemPalace: wing_miniclaw → wing_kaizen (32 drawers migrated)
#   - Env var prefix: MINICLAW_* → KAIZEN_*
#   - Backup tarball: ~/.miniclaw.backup.2026-05-13.tgz (delete after a few days)
```

Expected: drawer write succeeds; subsequent `mempalace_search` returns it.

---

### Task 19: Final cleanup (deferred)

**Files:** the backup tarball at `~/.miniclaw.backup.2026-05-13.tgz`.

This task is intentionally deferred — do NOT execute it as part of this plan run. It's listed here as the documented final step so it isn't forgotten.

- [ ] **Step 1 (deferred, run after a few days of confidence):** `rm ~/.miniclaw.backup.2026-05-13.tgz`

---

## Verification gates (summary)

| Gate | Task | Pass condition |
|---|---|---|
| Tests on renamed code | 5 | `pytest -q` exit 0 |
| Zero `miniclaw` in tracked files | 4 | `git grep -i miniclaw` empty |
| Local smoke | 8 | `--skill-select` runs cleanly against `~/.kaizen/` |
| CI on renamed repo | 9 | `gh run watch` reports green |
| MemPalace migration | 13 | new-wing count == baseline; old wing empty |
| End-to-end | 17 | sessions+memory write to `~/.kaizen/`; new save lands in `wing_kaizen` |

## Rollback

While `~/.miniclaw.backup.2026-05-13.tgz` exists:

- **After Task 5:** `cd ~/linux/miniclaw && git reset --hard rename/pre-kaizen`.
- **After Task 7:** as above, plus `mv ~/.kaizen ~/.miniclaw`, `mv ~/linux/kaizen-voice-pipeline ~/linux/miniclaw-voice-pipeline`, `mv ~/linux/kaizen ~/linux/miniclaw`, `git worktree repair`.
- **After Task 9:** `gh repo rename miniclaw --repo M8SON/kaizen`; reset both remote URLs.
- **After Task 13:** restore `~/.mempalace/knowledge_graph.sqlite3.pre-kaizen` and `~/.mempalace/palace/chroma.sqlite3.pre-kaizen` in place.

Once Task 19 runs, rollback of user data is no longer possible.
