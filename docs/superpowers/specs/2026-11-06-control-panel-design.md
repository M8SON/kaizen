# Control panel: manage Jarvis from a browser

**Date:** 2026-11-06
**Status:** Draft. Users decided 2026-11-06 (§3); §11 Q2–Q4 still open.
**Owner:** Mason
**Related:**
- `core/usage_log.py`, `scripts/cost_report.py`: the usage data behind the Costs page.
- `docs/experiments/2026-11-06-usage-metadata.md`: the one-call checks the Test buttons reuse.
- `docs/experiments/2026-11-03-dummy-output-after-boot.md`: the audio fault the Status page surfaces.
- `core/container_manager.py` `_execute_set_env_var`: the existing safe `.env` writer.

## 1. Problem

Everything Jarvis needs to run is set up over SSH today: API keys in `.env`,
backend choices, Spotify re-authorization, Wi‑Fi. Each of the last week's
outages needed a laptop and a terminal:

- **Wi‑Fi password change.** The Pi dropped off the network and needed an
  ethernet cable plus `nmcli`. Still not fixed on the Pi.
- **Dummy Output after boot.** Jarvis was silent while his logs looked
  healthy. Only `wpctl` showed it.
- **Expired Spotify token.** Fixing it meant SSH, the right Python, and
  pasting a redirect URL back into the terminal.
- **Monthly spend.** The only way to see it is to run a script on the Pi.

The goal is one page, opened from a phone, that covers all of these without
SSH, and does it without making the keys easier to steal.

The repo is public, and anyone should be able to clone it and build their own
assistant. A household that does that today has to edit `.env` by hand. Some
of the code also assumes the original owner (§3.1).

## 2. Decision: a web panel served by the Pi, not a native app

The panel is a small web app on the Pi at `http://<hostname>.local:8080`,
for example `http://daedalus.local:8080` on the original Pi, where it
already resolves over mDNS. Each install has its own hostname, so Jarvis
says the panel's address when you ask "what's your setup page?", and once
at the end of the first-boot setup.

- **Reaches every device with nothing to install.** Any phone or laptop
  browser works. It can be added to the home screen as a PWA.
- **No app store and no per-platform builds.** One codebase, written in
  Python like the rest of Kaizen.
- **Needs the Pi itself anyway.** A native app would still need an API on
  the Pi to talk to, so it would only add a client on top.
- **Revisit native only for phone-only features.** For example push
  notifications ("Jarvis lost his speaker"), or controlling him away from home.

## 3. Scope

**Users (decided 2026-11-06):** anyone who clones the repo to build their own
assistant.

- **One install per household,** private to that household, on its own Pi
  and home network, with its own API keys.
- **One admin, the person who set it up,** configures it for everyone.
  Everyone else in the house just talks to it and never needs the panel.
- **Built for the public, but not reachable by the public.** The repo is
  public and every install is private. No install is reachable from the
  internet, and there's no shared server or account.

So all three phases are in scope. Phase 3, first-time setup, is what makes
the repo usable by someone who isn't the original developer.

| Phase | Contents |
|---|---|
| 1 | Login; the four service slots (keys, Test, backend choice); Status and Restart; Costs |
| 2 | Spotify re-authorization; Wi‑Fi networks; recent logs |
| 3 | First-time setup on a new Pi: a setup hotspot with no Wi‑Fi yet, a guided key walkthrough |

**Out of scope:**
- Access from outside the home network.
- Multiple panel accounts. There is one admin password per install.
  Household members don't log in.
- Editing skills or memory notes. Obsidian already covers the notes.
- Any setting that isn't listed in §5.

### 3.1 Owner-specific code that has to become settings

A second household's assistant must not introduce itself as Mason's.

**The name part was done 2026-11-06, ahead of the panel.** `OWNER_NAME` (a
person or a household) now fills the system prompt and the `{owners}`
placeholder in the identity answers. When it's unset the wording is neutral:
"a personal voice assistant", "your voice assistant". `save-memory` says
"the user". No owner name is left in code. The panel and the wizard only
need to set `OWNER_NAME`.

**Location was done the same day.** `HOME_LOCATION` sets the home city.
`core/location_preference.resolve_location` uses it when no
`topic: location` memory note exists, so saying "I live in…" still wins.
That covers weather, the dashboard's local news query and the greeting. The
dashboard's built-in Vermont feeds and query are gone from its defaults, and
the examples are neutral. `local_vt` remains an opt-in feed group. The
wizard and panel only need to set `HOME_LOCATION`. If a memory note exists,
the panel should show it and offer to clear it, so the two can't silently
disagree.

**Separate change:** the persona name is the wake word, so it has to stay one
of openWakeWord's trained models. The wizard offers the bundled list, not
free text.

## 4. Architecture

```
phone/laptop ──HTTP (LAN)──▶ kaizen-panel.service (Flask + waitress, :8080)
                                │ reads/writes
                                ├── ~/kaizen/.env            (keys, backend choices)
                                ├── ~/.kaizen/usage.db       (read-only: costs)
                                ├── ~/.kaizen/panel.json     (password hash, session secret)
                                │ runs
                                ├── systemctl --user restart kaizen
                                ├── wpctl / nmcli            (status, Wi‑Fi: phase 2)
                                └── service self-checks      (Test buttons)
```

### Separate service

- **The panel is its own user systemd unit, `kaizen-panel.service`.** It runs
  from the same checkout and venv as Kaizen.
- **Neither can take down the other.** If Kaizen crashes, the panel is still
  up and can show the error and restart it. If the panel crashes, Jarvis
  keeps talking.
- **It doesn't import the orchestrator or open audio.** All it does is read
  and write files and run commands.

### Server

- **Flask behind waitress.** Both are pure Python, and waitress is a
  production WSGI server. Flask is already used by the dashboard skill, but
  only inside its container. Both packages are new to the host venv
  (`requirements.txt`).
- **Plain HTML templates with a little JavaScript** for the Test buttons and
  status polling. No frontend build step.

### Applying changes

- **Jarvis reads keys and backends once, at startup** (`main.py`). A
  changed value applies when Kaizen restarts.
- **Save shows a "Restart Jarvis to apply" banner** with a button, rather
  than restarting mid-conversation.
- **Restart goes through the existing unit**
  (`systemctl --user restart kaizen`). So `ensure_audio.sh` and the
  raspotify refresh also run on every restart from the panel.

## 5. Pages (phase 1)

### 5.1 Services

There are four slots, each a card.

| Slot | Backend choice (env var) | Key (env var) | Other settings |
|---|---|---|---|
| Brain | (Claude only) | `ANTHROPIC_API_KEY` | `CLAUDE_MODEL`, `FAST_MODEL` (choice list from `config/pricing.yaml` model names) |
| Voice | `TTS_BACKEND`: `elevenlabs` / `kokoro-onnx` | `ELEVENLABS_API_KEY` | `ELEVENLABS_VOICE_ID`, `TTS_SPEED` |
| Decision maker | `FILLER_CLASSIFIER_ENABLED` on/off | `TYPESAFE_API_KEY` | `FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD` |
| Listener | `STT_BACKEND`: `meta` / `whisper` | `META_MODEL_API_KEY` | (none) |

Brave and OpenWeather keys go in a fifth "Skills" card using the same
pattern. Only the variables listed here can be changed. The allowlist is
in code, like `set-env-var`'s check.

**Keys are write-only:**
- The page shows "set, ends in 1234" or "not set", never the value.
- Replacing a key means pasting a new one.
- There is no reveal button. If a key is lost, you get it again from the
  provider's console.

**Test buttons:**
- **What it tests:** the key in the input box if one was pasted, otherwise
  the saved key.
- **What runs:** one minimal billed call, the same ones that worked on
  2026-11-06:

  | Service | Test call | Cost |
  |---|---|---|
  | Claude | `messages.create`, `max_tokens=1`, on the chosen model | ~$0.00001 |
  | ElevenLabs | the existing `elevenlabs_self_check` | 1 character |
  | Jev | one `system_one` Choice question | ~300 tokens |
  | Meta | handshake, then `endStream` with no audio | no audio billed |

- **What it shows:** green, or red with the provider's error message
  ("missing permission", "invalid key", "quota exceeded").
- **Save requires a passing Test,** with a "save anyway" link for when the
  provider is down.

### 5.2 Status

- **Jarvis:** `systemctl --user is-active kaizen`, plus uptime and the last
  restart.
- **Speaker:** the default PipeWire sink, using the `ensure_audio.sh` check.
  Red when it is `auto_null` ("Dummy Output: Jarvis can't be heard. Restart
  to fix.").
- **Microphone:** whether the XVF3800 is listed in `/proc/asound/cards`.
- **Network:** interface in use (eth0 or wlan0), SSID, IP.
- **Restart Jarvis button.**
- Polled every 10 s while the page is open. Nothing runs in the background
  otherwise. That matches the earlier decision that a per-minute timer was
  overkill.

### 5.3 Costs

- **The same numbers as `scripts/cost_report.py`,** shown as a table.
- **The report needs refactoring first.** Its query and pricing move into a
  function that both the script and the page call, so the numbers can't
  diverge.
- **Month picker:** this month and the last three.
- **ElevenLabs allowance bar:** credits used of `plan_credits`.

## 6. Pages (phase 2)

### 6.1 Spotify

- **Button:** "Re-authorize Spotify". It opens Spotify's consent page with
  the redirect pointed at the panel (`/spotify/callback`), which writes
  `~/.kaizen/spotify-tokens.json`.
- **Prerequisite:** the panel's redirect URI has to be registered in the
  Spotify developer app.
- **Problem:** Spotify only allows plain `http` redirects to loopback
  addresses, not to `daedalus.local`. Two ways round it:
  - **Option A:** keep `http://127.0.0.1` as the redirect. The page asks the
    user to paste back the URL their browser ends up on, as
    `spotify_login.py` does today, but into a text box.
  - **Option B:** HTTPS on the panel. That brings a self-signed certificate
    warning on every phone.
- **Choice:** A. It's clunky but works with no setup.

### 6.2 Wi‑Fi

- **Shows:** a list of visible networks and the saved ones, with "Connect"
  and a password field.
- **How:** it runs `nmcli` as the `archimedes` user. The Pi's NetworkManager
  polkit rules decide whether that works without sudo; if not, add a narrow
  polkit rule rather than sudo.
- **Safety:** after a change the panel keeps the old connection profile for
  60 s. If the new network doesn't come up, it switches back.
- **Caveat:** changing networks drops the page. If the phone is on the same
  network, it reconnects on its own.

### 6.3 Logs

- **What:** the last 200 lines of Kaizen's journal, refreshed on demand,
  with secrets masked.
- **Caveat:** the Pi's journal is volatile, so logs from before a reboot
  aren't available. Making it persistent is a separate one-line change,
  noted in §10.

## 7. Security

The panel holds every key, so it has to be no easier to attack than SSH.

### Who can reach it

- **Home network only.** It refuses requests whose client address isn't
  loopback or a private range (10/8, 172.16/12, 192.168/16, fc00::/7).
- **Never internet-exposed.** Port-forwarding to it is documented as
  unsupported.

### Login

- **Password set on first visit.** The first page shown is "Set a
  password", reachable only while `panel.json` has no password.
- **Stored as a scrypt hash** (`hashlib.scrypt`, standard library).
- **Sessions:** a signed cookie that is HttpOnly and SameSite=Strict,
  expiring after 30 days.
- **Rate limit:** 5 failed logins per minute.
- **Gap in the first-visit rule:** anyone on the network who opens the
  page before the owner can claim it. To close it, Jarvis speaks a
  six-digit setup code at startup while no password is set, and the page
  asks for that code. Being able to hear Jarvis proves you're in the room.

### Forged requests

- **Every change is a POST that needs a CSRF token** tied to the session.
- **SameSite=Strict** cookies are a second layer.

### Keys

- **Never sent back to the browser.** Only the last four characters are
  shown.
- **Never logged, including Test errors.** Provider error bodies are
  filtered for the key before they're shown.

### `.env`

- **Written atomically:** temp file, `fsync`, `rename`, with mode `0600`.
- **Already partly done (2026-11-06):** `.env` and its nine `.env.bak.*`
  backups on the Pi were `644`, readable by every account on the Pi. All are
  `600` now, and `run.sh` sets `600` on them at every start. `.gitignore`
  now covers `.env.*` (except `.env.example`), since the backups weren't
  ignored and a `git add -A` would have published the keys.
- **Backups:** the panel writes its own backups as `.env.bak.<timestamp>`,
  also `0600`, and keeps only the last five.
- **Same rules as `set-env-var`:** the value is rejected if it contains a
  newline, and only allowlisted keys can be written.

### Transport

- **Plain HTTP on the home network.** Anyone who can watch home Wi‑Fi traffic
  could see a pasted key. That's acceptable for phases 1 and 2 on a WPA2/3
  home network.
- **Phase 3, or any shared network, needs HTTPS** (§11).

### Commands

- **Fixed argument lists, no shell, no user input** in `systemctl`, `wpctl`
  or `nmcli` arguments. The only exception is the Wi‑Fi SSID and password,
  which are passed as separate `argv` entries.

## 8. Phase 3: first-time setup

This phase is in scope (§3). It's how someone who isn't the developer gets
from a cloned repo to a working assistant.

- **Setup hotspot.** With no network configured, the Pi broadcasts a
  "Jarvis-Setup" hotspot (NetworkManager `wifi.mode ap`). Jarvis says
  "Connect to Jarvis-Setup on your phone."
- **Captive page.** The panel's setup wizard opens, in order:
  1. Wi‑Fi
  2. the setup code Jarvis speaks, then the admin password
  3. owner or household name, and wake word / persona (§3.1)
  4. location (for weather)
  5. the four keys with Test buttons
  6. a test sentence

  Keys a household doesn't want are skipped by picking the local backend:
  Kokoro for voice, Whisper for listening, and no decision maker. Claude is
  the only key that is required.
- **Install.** One command, or a prebuilt SD card image, with everything
  installed and the services enabled.
- **This is most of the work.** The hotspot and captive-portal behaviour
  differs between Android and iOS, and needs testing on both.

## 9. Testing

**Unit tests:**
- the `.env` writer (atomic write, 0600, allowlist, newline rejection);
- that keys are masked in every response and log line;
- login, CSRF and the private-address check;
- that the Costs page shares numbers with `cost_report.py`.

**On the Pi:**
- every Test button with a good key and a bad one;
- Save, then Restart, then Jarvis speaking on the new backend;
- an injected Dummy Output (the `aplay` method from 2026-11-03) shows red on
  Status, and Restart clears it;
- the page loads from a phone on the home Wi‑Fi.

Each run is recorded under `docs/experiments/`.

## 10. Small changes this enables (could ship first)

- `chmod 600 ~/kaizen/.env` on the Pi now, and in `install_systemd_service.sh`.
- Persistent journal on the Pi (`Storage=persistent`), so the Logs page and
  future debugging can see past reboots.
- Fix the stale `Host pi` IP in the laptop's `~/.ssh/config`
  (`192.168.1.137` → `daedalus.local`).

## 11. Open questions

1. ~~Who are the users?~~ **Decided 2026-11-06:** anyone who clones the
   repo; one private install per household; one admin configures it (§3).
   Still to decide under this: is plain HTTP on the home network acceptable
   for a public release (§7, Transport)? The alternative is a self-signed
   certificate with a one-time warning per phone.
2. **Spotify: is the paste-the-URL flow (§6.1) acceptable?** If not, it's
   HTTPS with a certificate warning.
3. **Should Save restart Jarvis automatically** when he's idle (no session
   in progress)? The alternative is always asking first.
4. **Other settings:** should wake-word sensitivity
   (`WAKE_WORD_THRESHOLD`) or speech speed be on the page? They are not
   keys, but they are what people tend to want to adjust.
