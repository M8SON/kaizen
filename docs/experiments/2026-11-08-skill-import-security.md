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
