# Skill import security review (2026-11-08)

**Question:** Is it safe to deploy the panel's "Build a skill" and "install from a link" boxes? Can a downloaded skill get past the checks before it is approved?

**Method:** Read the panel routes and the import path. Reproduced each suspected bypass against commit `c2a4cdd` plus the uncommitted panel change, fixed it, then re-ran it on the Pi against `736584d` with real Docker, using a temporary `KAIZEN_PANEL_DIR` and local-folder imports (harmless test skills, rejected and cleaned up afterwards).

## Before the fix

| Check | Result |
|---|---|
| `RUN pip install x \` then `&& touch /proof` | accepted; on the Pi `/proof` was created by a real build |
| `RUN pip ... \| sh`, `$(...)`, `SHELL`, `ONBUILD`, `# syntax=` | accepted at authored and imported tiers |
| Skill named `base` with image `kaizen/base:latest` | accepted; approving would overwrite the shared base image |
| Skill named `builder-claude-code` with the sandbox's tag | accepted; approving would replace the image given `ANTHROPIC_API_KEY` |
| Import listing `BRAVE_API_KEY` | key passed to its tests before approval (read from the code; not run) |
| Panel link box with a folder path | accepted by the route; only the browser `pattern` stopped it |

## After the fix (Pi, `736584d`)

| Import | Result |
|---|---|
| Continuation bypass | failed at validation, no image built |
| Skill named `base` | failed: name reserved; `kaizen/base:latest` unchanged (`sha256:3199d82f0ba0`) |
| Listing `BRAVE_API_KEY`, valid multi-line `pip install 'requests>=2'` | built and ready; tests not run, note shown |
| No keys | ready; 1 of 1 tests passed, output `value no-key` |
| Candidate images left after reject | 0 |

Panel on the Pi, not logged in: `GET /skills` 302 to login, `GET /api/skills` 401, `POST /skills/build` 302 to login. From a laptop on the LAN, `POST /skills/import` 302 (not logged in, nothing started).

Laptop: 860 tests pass. A real `.tar.gz` from files.pythonhosted.org came through `fetch_skill`. `skills/*/scripts/Dockerfile` at authored tier: all pass except `playwright-scraper` (`playwright install`), which is bundled and not validated.

**Conclusion:** All reproduced bypasses are closed. Login, CSRF, escaping and the job-id check were already sound.

**Accepted, not fixed:**
- A skill's build and tests run before approval, in containers with network access and no secrets (for imports). A malicious pip package can still run code in the build container and reach the LAN.
- Agent-written skills' tests get the non-reserved keys they list (on the Pi: Brave, OpenWeather).
- A logged-in panel user can make the Pi fetch from any https host, including on the LAN.
- `git clone` has a 180 s limit but no size limit.

**Not tested:** a real https git import on the Pi; approving from a real browser; `# syntax=` running a frontend under BuildKit (rejected before Docker is called, so not attempted); `_download` limits against a real oversized or slow server.

## Follow-up: what a `docker build` step gets on the Pi

**Question:** What limits does a skill's image build actually run under, and which `docker build` flags take effect?

**Method:** On the Pi (Docker 26.1.5, buildx 0.13.1), a throwaway image whose `RUN` step printed its uid, cgroup limits, effective capabilities, and tried TCP to 1.1.1.1:443, the gateway 192.168.1.1:443 and the Pi's own LAN address on port 22 (3 s timeout).

| Build | uid | memory.max | pids.max | CapEff | 1.1.1.1:443 | gateway:443 | Pi:22 |
|---|---|---|---|---|---|---|---|
| BuildKit, default | 0 | max | max | a80425fb | reachable | timeout | timeout |
| BuildKit, `--network=none` | 0 | max | max | a80425fb | no network | no network | no network |
| BuildKit, `--memory=1g` | 0 | max | max | a80425fb | reachable | timeout | timeout |
| Legacy builder, `--memory=1g --network=none` | 0 | 1073741824 | 9575 | a80425fb | no network | no network | no network |

**Conclusion:** BuildKit ignores `--memory` without an error; only the deprecated legacy builder applies it. `--network=none` works on both, but pip and apt need the network. Builds run as root with Docker's default capabilities. The LAN timeouts are one port on each of two hosts, so they don't show the LAN is blocked.

**Not tested:** `PIP_ONLY_BINARY=:all:` during a real skill build; other LAN hosts or ports.

## Follow-up: wheels-only builds (Pi, `2d90862`)

**Question:** With `PIP_ONLY_BINARY=:all:` and the new Dockerfile rules, do normal skills still build, and is source code refused?

**Method:** Real local-folder imports through `skill_builder` with real Docker, temporary `KAIZEN_PANEL_DIR`, all rejected and cleaned up. Then `pip` directly in `kaizen/base`, and one real `docker build` of `sgmllib3k` with the same `ENV` line.

| Case | Result |
|---|---|
| `pip install pyyaml six` (C extension + pure Python, aarch64) | ready; 1 of 1 tests passed; image reports `PIP_ONLY_BINARY :all:` |
| `pip install feedparser` | built: 6.0.14 and `feedparser-sgmllib` 2.1.0, both wheels |
| `COPY pip /usr/local/bin/pip` before `RUN pip install` | failed validation, `RUN must come before COPY and ENV` |
| `pip install --no-binary=:all: six` | failed validation |
| `sgmllib3k` (PyPI lists only `sgmllib3k-1.0.0.tar.gz`) | `No matching distribution found` |
| Candidate images left | 0 |

**Conclusion:** Source packages are refused and common wheels install. I expected `feedparser` to fail on a source-only dependency; it no longer has one. Laptop: 863 tests pass.

**Not tested:** a paid agent build with the new prompt rules; a real import of a community Dockerfile (ones that `COPY requirements.txt` before `RUN pip install` are now refused). `core/skill_cli` terminal installs get the Dockerfile rules but not the wheels-only build.
