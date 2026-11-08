# Skill builder: any coding agent, sandboxed, tested before install

Status: step 1 built (2026-11-07), plus the panel's "Build a skill" and
"install from a link" boxes from step 4: Docker sandbox, Claude Code, test/repair loop,
and approval on the panel instead of spoken gates (§4, as changed below). Steps 2–4 not started. Extends voice skill installation (`core/meta_skill.py`,
`core/install_pipeline.py`) and the control panel's Skills page
(`2026-11-06-control-panel-design.md` §6.4).

## 1. Problem

Today "add a skill that does X" runs Claude Code once
(`meta_skill._run_claude_code`):

- **Claude Code only.** The sandbox is Claude Code's own permission flags
  (`--allowedTools`, `--disallowedTools Bash`), so no other agent fits.
- **Never tested.** Bash is denied, so the agent can't run `app.py`. A
  broken Dockerfile or script fails at the build gate, and nothing repairs it.
- **One shot.** One spoken sentence in, no questions back, a 5-minute limit.
- **No edits.** An authored skill can't be changed afterwards.
- **Voice-only review.** The user hears a permission summary but never sees
  the files.
- **Unused.** `~/.kaizen/authored` on the Pi is empty; the flow has never
  run end to end on real hardware.

## 2. Decision: sandbox the agent with Docker, not with its own flags

Each coding agent runs inside a throwaway container, so the security doesn't
depend on any agent's permission system, and any agent with a
non-interactive mode works the same way.

- **Mounted:** the staging directory at `/work`, read-write. Nothing else:
  no repo, no `~/.kaizen`, no `.env`, no Docker socket.
- **Secrets:** only the chosen agent's own credential (an API key variable,
  or a copy of its login files; §3).
- **Inside, the agent may do anything:** run a shell, `pip install`, run
  `app.py` against test inputs. That's what lets it test its own work.
- **Limits:** the same `--cap-drop=ALL`, `no-new-privileges`, non-root user,
  memory and pids limits as skill containers, and a wall-clock timeout
  (default 10 minutes).
- **Network:** open egress, since the agent needs its API and `pip`. A
  prompt-injected agent could send out what's in the container: its own key
  and the staging files. Nothing else is there.
- **Kept:** the agent's own permission flags are still passed where they
  exist (scoped to `/work`), as a second layer.

## 3. Agents are configuration

`config/coding_agents.yaml` lists the agents. Adding one is a YAML entry and
a Dockerfile, not code.

```yaml
claude-code:
  image: kaizen/builder-claude-code      # config/builders/claude-code.Dockerfile
  command: [claude, -p, "{prompt}", --output-format, json]
  credential: {env: ANTHROPIC_API_KEY}
codex:
  image: kaizen/builder-codex
  command: [codex, exec, --sandbox, workspace-write, "{prompt}"]
  credential: {env: OPENAI_API_KEY}
grok:
  image: kaizen/builder-grok
  command: [grok, -p, "{prompt}"]
  credential: {env: XAI_API_KEY}
muse:
  image: kaizen/builder-muse
  command: [muse, ...]                   # headless form to be confirmed
  credential: {login_dir: ~/.muse}       # subscription login, copied per run
```

- **`{prompt}`** is the only placeholder; arguments are a list (no shell).
- **`credential.env`:** read from `.env` and passed to that container only.
  The key is added to the panel's Services page (write-only, tested).
- **`credential.login_dir`:** for agents that use a subscription login
  instead of a key (SuperGrok, Muse plans). The user logs in once on the Pi
  (`! grok login` style, in their own terminal); the builder copies the
  directory into the container per run and never back.
- **Builder images** are built on first use, like skill images, from
  `config/builders/<agent>.Dockerfile`. They aren't skill images and aren't
  checked by `dockerfile_validator` (they're bundled code).
- **Choosing:** `SKILL_BUILDER` (default `claude-code`), a dropdown on the
  panel listing agents whose credential is present, or by voice ("use codex
  to build a skill that...").
- **Flags above are from each tool's docs as of 2026-11 and are unverified on
  the Pi.** Before an agent ships: confirm it has a Linux arm64 build, its
  headless flags, and its auth (§8).

## 4. Build, test, repair

```
describe ─► agent writes files ─► check tree + validate (authored tier)
        ─► build candidate image ─► run tests ─┬─ pass ─► review ─► install
                                               └─ fail ─► agent fixes (≤2 more rounds)
```

1. **Write.** The agent gets the same prompt as today plus the rule to
   write `references/tests.json`: two to five inputs with what the output
   should contain, and to try them in its container before finishing.
2. **Check.** `_check_staged_skill` and `InstallPipeline` validation at the
   authored tier, unchanged (Dockerfile allowlist, clamps, reserved secrets).
3. **Build.** `scripts/build_new_skill.sh` as `kaizen/<name>:candidate`. Only
   after validation passes, so only allowlisted Dockerfile instructions run.
4. **Test.** Each test input runs through `container_manager` exactly as a
   real call would (same flags, `--pull=never`, timeout). Output checks are
   "contains" matches, so tests needing live data still pass.
5. **Repair.** On a failed build or test, the same agent runs again with the
   error text and the files as they are. At most 3 rounds in total, then it
   stops and says what failed.
6. **Review and install, in the browser.** No spoken gates: asking starts
   the build. When it's ready Jarvis says so, and the panel's Skills page
   shows the permission summary, test results and every file, with Approve
   and Reject. Approve installs only if the files still hash to what was
   tested, retags the candidate image, and restarts Kaizen once idle (the
   panel can't call `reload_skills()`). The candidate is built before
   review, but only after validation, and is deleted on Reject. Installs
   from a URL or path work the same way without the agent (imported tier,
   the skill's own tests if it ships any, and no keys until approved). The user wants no voice approval
   at all, only the panel.

## 5. Editing a skill

- "Change the joke skill to tell shorter jokes": the authored or imported
  skill is copied into staging, the agent edits it, and the same
  check → build → test → review flow runs.
- **Review shows the change:** spoken as a short summary ("changed app.py,
  added one test"), shown as a diff on the panel.
- **Bundled skills** (`./skills/`) aren't editable this way; they're repo
  code.
- The previous version is kept as `~/.kaizen/authored/<name>.prev/` for one
  undo.

## 6. On the panel

The Skills page (§6.4 of the panel spec) gets a "Build a skill" form: a text
box (longer than a spoken sentence), the agent dropdown, then live progress
(write, build, test, repair rounds), the files and test results, and
Install / Cancel buttons. A build started by voice shows up there too, so it
can be reviewed on a screen; whichever answers first, voice or panel, wins
the gate.

## 7. Cost

Each run is logged to the usage log with purpose `skill-build` and the
agent's name. Claude Code's JSON output reports its own cost and tokens;
other agents log what they report, and show as "no price" on Costs until
they're in `config/pricing.yaml`. Subscription agents cost nothing per call
on top of their plan.

## 8. To check before building

- Arm64 Linux builds and headless invocation, on the Pi, for: Claude Code
  (installed, 2.1.295), Codex (`codex exec`), Grok Build (`grok -p`), Muse
  Code (not yet known).
- How each authenticates headless: API key variable vs a login that can be
  copied into a container.
- Whether Codex's own Linux sandbox (Landlock) runs inside Docker, or needs
  to be turned off there.
- One run of today's flow on the Pi, to see where it actually fails, recorded
  in `docs/experiments/`.

## 9. Order

1. **Docker sandbox + Claude Code + test/repair loop.** Same agent as today,
   so the change is the sandbox and the tests.
2. **Other agents:** Codex, Grok, Muse, each after its §8 check.
3. **Editing skills.**
4. **Panel build and review UI**, with Skills, Memories and spend at a
   glance from the panel spec.
