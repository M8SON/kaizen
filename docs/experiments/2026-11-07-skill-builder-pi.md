# 2026-11-07 Skill builder on the Pi

## Question

1. Where does today's voice authoring (one Claude Code run, Bash denied) fail
   on the Pi, before replacing it?
2. Does the new builder (`core/skill_builder.py`) work on the Pi: Claude
   Code in a Docker sandbox writing and testing a skill, then Kaizen
   validating, building a candidate and rerunning the tests?

## Method

- Pi `daedalus` (Raspberry Pi 5, aarch64), Docker, Claude Code 2.1.295.
  Live Kaizen (`71a896f`) kept running and untouched.
- **Baseline:** `meta_skill._run_claude_code` from `71a896f`, then
  `_check_staged_skill`, `SkillValidator` at the authored tier, the
  Dockerfile validator and `docker build` as `:candidate`; the image run with
  Kaizen's container flags (`--cap-drop=ALL --read-only`, host uid,
  `--tmpfs /tmp`). Two descriptions: "convert temperatures between celsius
  and fahrenheit", "tell me the current price of bitcoin and ethereum in
  dollars".
- **Sandbox prototype:** `config/builders/claude-code.Dockerfile`
  (kaizen/base + Debian nodejs/npm + `@anthropic-ai/claude-code@2.1.295`),
  `claude -p … --output-format json --dangerously-skip-permissions` with only
  the staging dir mounted, asked for a dice-roller skill with
  `references/tests.json` and told to run the tests.
- **End to end:** the uncommitted working tree rsynced to
  `~/kaizen-preview`, scratch `KAIZEN_PANEL_DIR`, `skill_builder.start(
  "tell me a random fact about cats", "random-cat-fact")` with the real
  launcher (systemd-run). Not approved, since approval installs into the
  live `~/.kaizen/authored`. The panel page was rendered on the laptop from a
  copy of that job (1280 and 390 px). Preview, candidates and scratch data
  deleted afterwards.

## Results

Baseline (today's flow):

| Skill | Claude Code | Tree / validation | Build | Run |
|---|---|---|---|---|
| temperature-converter | ok, 23 s | pass | ok, 1 s | "100 degrees Celsius is 212 degrees Fahrenheit." |
| crypto-prices | ok, 22 s | pass | ok, 5 s | live prices from CoinGecko |

Both ran first time; the agent itself noted it hadn't run or built anything.

Sandbox prototype:

- Sandbox image built in 2 min 2 s, 1.02 GB (Node 20.19.2, Python 3.11.15).
- dice-roller: 35 s, 4 turns, `claude-opus-5-5`, `total_cost_usd`
  0.119; wrote 9 tests and ran each 20 times (random output), all passing.
- JSON output carries `modelUsage` per model (input, output, cache read,
  cache creation tokens, costUSD); that is what the builder logs.

End to end (random-cat-fact):

| Step | At |
|---|---|
| claude-code writing | 0 s |
| image built | 27 s |
| tests run in the real skill container | 28 s |
| 2 of 2 passed, ready | 30 s |

- One round; the tests fetched live facts from catfact.ninja, with a local
  fallback list in the code.
- Usage row: `claude-opus-5-5`, 8 in / 2,225 out / 62,231 cache read /
  8,700 cache write tokens. At `config/pricing.yaml` prices that is $0.127;
  Claude Code's cache writes are 5-minute (1.25x), not the 1-hour 2x the
  file assumes, so the real cost is about $0.10.
- The page showed the permissions, both tests, every file (HTML escaped) and
  Approve / Reject at both widths.

After deploying `febb18f` (no voice approval at all; imports as jobs too):

- Launching from inside a systemd user service:
  `systemd-run --user --wait -- sh -c "systemd-run --user --collect … true"`
  → ok, so Kaizen's service can start build units.
- `scripts/test_install_skill_integration.py` (real Claude Code in the
  sandbox, approve into a temp authored dir, load): "hello message" skill,
  one round, 3 of 3 tests passed in the real container, approved and loaded
  by `SkillLoader`. Temp dirs and images removed; `~/.kaizen/authored`
  untouched.
- Import of a local folder (a renamed copy of `skill-tells-random`, scratch
  dirs): ready at the imported tier, "doesn't ship tests" noted, approved
  into the temp imported dir with `.install.json`.

## Conclusion

Writing skills was never the weak part: simple and web-API skills worked
first time with the old flow. The new builder works on the Pi end to end in
about 30 s plus a one-off 2-minute sandbox build, and the agent now tests
its own code. The repair loop hasn't had a real failure to repair yet.

## Not tested

- A real repair round (only unit-tested with a scripted agent).
- Approve from the real panel page on the Pi, and Kaizen loading the skill
  after its restart (approve + load tested via the integration script).
- Asking by voice, and Jarvis announcing the finished build.
- Skills that need an API key (tests are skipped when the key is missing).
- Imports from an https git URL or tarball on the Pi (unit-tested; the
  fetch code is the old `install_from_url`'s).
- Other agents (step 2).
