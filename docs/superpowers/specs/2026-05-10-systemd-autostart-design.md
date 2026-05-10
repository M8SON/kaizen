# MiniClaw systemd auto-start (Pi boot)

**Date:** 2026-05-10
**Status:** Approved, pending implementation plan
**Owner:** Mason

## Goal

Make MiniClaw start automatically on Pi 5 boot so the device behaves like a finished appliance: power on, wait for the wake word, talk. No manual `./run.sh --voice` from a shell.

## Non-goals

- Auto-update from git on boot.
- Process watchdog beyond systemd's restart-on-failure.
- Health-checking Docker daemon (`run.sh` already probes Docker).
- A web UI / remote control surface for the service.

## Architecture

A single user-level systemd unit running under `archimedes` on the Pi, made boot-persistent via `loginctl enable-linger`.

User-level (vs. system-level) was chosen because the Pi's audio stack runs in the user session: PipeWire-pulse + the SoundCloud/Spotify clients all expect `XDG_RUNTIME_DIR` and the user's PulseAudio socket. Running MiniClaw inside the same user session avoids the runtime-dir/audio-group workarounds a system-level unit would need.

Boot flow:

1. Pi boots.
2. systemd brings up `network-online.target`.
3. Because of `loginctl enable-linger archimedes`, systemd starts the user manager for `archimedes` without an interactive login.
4. The user manager activates `miniclaw.service` (it's in `default.target`'s wants).
5. The unit's `ExecStart` runs `~/miniclaw/run.sh --voice`.
6. `run.sh` validates venv/Docker/deps, then `exec`s `python main.py --voice`.
7. If the process exits non-zero, systemd waits 5s and restarts.

## Components

### 1. Unit file — `config/systemd/miniclaw.service`

Checked into the repo as the source of truth.

```ini
[Unit]
Description=MiniClaw voice assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/miniclaw
ExecStart=%h/miniclaw/run.sh --voice
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Notes:
- `%h` resolves to the user's home, so the same unit works for any user that clones MiniClaw into `~/miniclaw`.
- `Type=simple` — `run.sh` ends in `exec python3 main.py …`, so the python process becomes the unit's main PID.
- `Restart=on-failure` (not `always`) so `systemctl --user stop miniclaw` is honored.
- `RestartSec=5` covers the typical USB audio enumeration delay (mic/DAC usually present within 1–2s of boot).
- No `StandardOutput=` / `StandardError=` — defaults send both to the user journal, which is exactly what we want.

### 2. Installer — `scripts/install_systemd_service.sh`

One-shot script the user runs once on the Pi. Idempotent (safe to re-run after pulling a new unit file).

Responsibilities, in order:

1. Resolve `SCRIPT_DIR` → repo root; refuse to run if `run.sh` not adjacent.
2. Create `~/.config/systemd/user/` if missing.
3. Copy `config/systemd/miniclaw.service` → `~/.config/systemd/user/miniclaw.service`.
4. Check `/var/log/journal`; if missing, prompt and run `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald` so journal logs survive reboot. Skip silently if already present.
5. Check `loginctl show-user $USER --property=Linger`. If `Linger=no`, prompt and run `sudo loginctl enable-linger $USER`. Skip if already enabled.
6. `systemctl --user daemon-reload`.
7. `systemctl --user enable --now miniclaw.service`.
8. `systemctl --user --no-pager status miniclaw.service` and print the journalctl one-liner: `journalctl --user -u miniclaw -f`.

The script uses `sudo` only for the two host-level operations (journal dir, linger). Everything else is user-scoped.

### 3. Uninstaller — `scripts/uninstall_systemd_service.sh`

Symmetric tear-down. Steps:

1. `systemctl --user disable --now miniclaw.service` (ignore "not loaded" errors).
2. `rm -f ~/.config/systemd/user/miniclaw.service`.
3. `systemctl --user daemon-reload`.
4. Ask whether to also `sudo loginctl disable-linger $USER` (default: no, since other user services may rely on it).

### 4. README addition

A new "Run on boot (Raspberry Pi)" section under the existing setup docs, covering:

- One-time install: `./scripts/install_systemd_service.sh`.
- Day-to-day: `systemctl --user {start,stop,restart,status} miniclaw`.
- Live logs: `journalctl --user -u miniclaw -f`.
- Crash investigation: `journalctl --user -u miniclaw --since '1 hour ago' -p err`.
- Uninstall: `./scripts/uninstall_systemd_service.sh`.

## Failure modes

| Failure | Handled by |
|---------|------------|
| USB mic/DAC not yet enumerated at first start | `Restart=on-failure` + 5s delay; MiniClaw fails fast on device-resolve, retries until USB is up |
| Network not yet up | `After=network-online.target` blocks start until network is reachable |
| Wifi drops mid-session, Claude API call fails | Process exits, journal records the error, systemd restarts after 5s |
| Process hangs without exiting | **Not handled.** Out of scope; revisit if observed |
| User wants to stop the service | `systemctl --user stop miniclaw` — won't auto-resurrect (Restart=on-failure only) |
| Pi reboots during a turn | Cold start path takes over on next boot |

## Testing

- **Local (dev workstation):** install the unit on the dev box (without `enable-linger`); confirm `systemctl --user start miniclaw` brings up the text or voice loop, `stop` halts it cleanly, and a forced `kill -9` triggers the 5s restart.
- **Pi (real hardware):** run the installer, reboot, confirm MiniClaw responds to the wake word with no shell intervention. Pull the USB mic mid-run and confirm the journal records the error and systemd retries.
- **Crash logging:** force a Python exception (e.g. temporarily raise in `main.py` startup), reboot, confirm `journalctl --user -u miniclaw -p err` shows the traceback.

## Open questions

- **`network-online.target` requires a wait-online service.** On Raspberry Pi OS Bookworm (NetworkManager-based), `NetworkManager-wait-online.service` is enabled by default and the target works as expected. The installer should verify this with `systemctl is-enabled NetworkManager-wait-online.service` (or the systemd-networkd equivalent) and warn if neither is enabled. No spec change needed — this is an implementation-time check.

## Risks

- **Linger has side effects.** Enabling linger means *every* user service is kept alive across logout, not just MiniClaw. Acceptable on a dedicated Pi appliance.
- **`run.sh` does setup work at every start.** The Docker/venv probes add ~1s to startup. Acceptable for now; if it becomes a problem, factor a `scripts/boot.sh` lean entry point and switch the unit's `ExecStart`.
- **Journal disk usage.** Persistent journal grows. systemd's defaults (`SystemMaxUse=10%` of /var) are fine; not changing them here.
