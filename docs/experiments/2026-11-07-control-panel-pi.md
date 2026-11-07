# 2026-11-07 Control panel phase 1 on the Pi

## Question

Does the phase 1 panel (`panel/`) work against the real services and the
real Pi before it is deployed: the four Test calls with real and fake keys,
the status checks, and the HTTPS setup → login → Test flow?

## Method

- Code: uncommitted working tree on top of `a5fb77d`, rsynced to
  `~/kaizen-panel-preview` on the Pi (`daedalus`, Raspberry Pi 5), run with
  the Pi's venv (`~/kaizen/.venv`, Python 3.13) after
  `uv pip install flask cheroot` (Flask 3.1.3, cheroot 11.1.2).
- Live Kaizen (`kaizen.service`, older code) kept running; the preview used
  a scratch `KAIZEN_PANEL_DIR` and a 0600 copy of `.env`, deleted after.
  Save was not pressed, so nothing restarted Jarvis and the live `.env` was
  never written.
- Test calls: `panel.checks.check_*` with the keys in `~/kaizen/.env`, then
  with fake keys (`sk-ant-api03-notarealkey…`, `notarealkey…`).
- HTTPS flow: `python -m panel`, then curl on the Pi against
  `https://127.0.0.1:8443` with a cookie jar.
- Laptop: the same server on `archimedes`, probed with curl and
  `openssl s_client`; pages rendered and screenshotted with headless
  Chromium at 1280 px and 390 px wide.

## Results

Test calls (real keys):

| Service | Result |
|---|---|
| Claude (`claude-sonnet-4-6`, `max_tokens=1`) | ok, "Claude answered" |
| ElevenLabs (`eleven_flash_v2_5`, text ".") | ok, first chunk received |
| Jev (`system_one`, one Choice) | ok |
| Meta (handshake + `endStream`, no audio) | ok, "Meta accepted the key" |

Fake keys, as shown to the user:

| Service | Message |
|---|---|
| Claude | `Invalid key: Error code: 401 - … authentication_error … invalid x-api-key` |
| ElevenLabs | first `Invalid key: headers: {'date': …}` (the SDK's `str()` is the response headers); after using `exc.body["detail"]["message"]`: `Invalid key: Invalid API key` |
| Jev | `Invalid key: Cannot authenticate with the server…` (after adding "authenticat" to the matcher; before, it was labelled `TypeSafeAuthenticationError`) |
| Meta | `Invalid key: Unauthorized: the credential presented in the handshake was not accepted` |

Status checks on the Pi:

- Jarvis: `active`, since `Fri 2026-11-06 19:36:19 EST`, 0 restarts; state
  `unknown` (the running Kaizen predates `core/run_state.py`).
- Speaker: `reSpeaker XVF3800 4-Mic Array Analog Stereo`, not Dummy Output.
- Microphone: `USB-Audio - reSpeaker XVF3800 4-Mic Array`.
- Network: `eth0`, `192.168.1.135`.
- Wake scores: read through `journalctl _SYSTEMD_USER_UNIT=kaizen.service`
  (the user journal has no files on this Pi; `archimedes` is in `adm`).
  Last five: 0.09 missed, 0.16 soft wake, 0.11, 0.08, 0.13 missed. Older
  hard wakes have no score in the log; they do from this change on.

HTTPS flow on the Pi:

- New certificate in about a second; SANs `daedalus.local`, `daedalus`,
  `localhost`, `127.0.0.1`, `192.168.1.135` (Docker's `172.17.0.1` left out).
- `/setup` with the code from `panel.json` → 302 to `/`, logged in.
- Services page: six keys, all shown only as "set, ends in xxxx".
- `POST /api/test/claude` with the saved key → `{"ok": true}`.
- Same POST without the CSRF header → 400. No cookie → 302 to `/login`.
- `/costs`: $6.01 for 2026-11 and $0.0005 per request, the same as
  `scripts/cost_report.py` on the same database.
- The Anthropic key appeared 0 times in the page and 0 times in the log.

Laptop:

- `http://127.0.0.1:8080/costs` → `308` to `https://127.0.0.1:8443/`.
- The fingerprint shown on the login page matched
  `openssl x509 -fingerprint -sha256`.
- `scripts/cost_report.py` output was byte-identical before and after
  moving its logic to `core/cost_summary.py` (this month, `2026-11`,
  `--days 7`, an empty month), against a copy of the Pi's `usage.db`.
- Test suite: 814 passed.

## Conclusion

Phase 1 works against all four real services and the Pi's real hardware
checks. Two error messages were rewritten after seeing the real provider
responses. Deploying needs the firewall update (8080 and 8443 from the home
network), which needs sudo on the Pi.

## Not tested

- A real browser on the home network (blocked by the firewall until it is
  updated), on iOS, Android or Windows; the certificate warning flow.
- Save → restart of the live Kaizen, and the idle wait during a real
  conversation (unit-tested with a fake systemctl only).
- Jarvis speaking the setup code (unit-tested; needs the deploy).
- Spotify (phase 2).
