# Kaizen systemd auto-start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a user-level systemd unit + installer/uninstaller scripts so Kaizen starts on Pi boot and survives crashes.

**Architecture:** One unit file (`config/systemd/kaizen.service`) checked into the repo as source of truth. A `scripts/install_systemd_service.sh` script copies it into `~/.config/systemd/user/`, ensures `loginctl enable-linger` and persistent journal, then enables the unit. A symmetric uninstaller. README gets a "Run on boot" section.

**Tech Stack:** systemd (user mode), bash, existing `run.sh` entry point. Static-content unittest assertions (matching `tests/test_run_sh.py` style) verify the unit file and scripts contain the load-bearing directives.

**Spec:** `docs/superpowers/specs/2026-05-10-systemd-autostart-design.md`

---

## File Structure

| File | Purpose |
|------|---------|
| `config/systemd/kaizen.service` (new) | Source-of-truth unit file. Uses `%h` so it's user-portable. |
| `scripts/install_systemd_service.sh` (new) | Idempotent installer. Handles linger, journal dir, daemon-reload, enable. |
| `scripts/uninstall_systemd_service.sh` (new) | Symmetric tear-down. |
| `tests/test_systemd_unit.py` (new) | Static-content assertions on the unit file. |
| `tests/test_install_systemd_service.py` (new) | Static-content assertions on the install/uninstall scripts. |
| `README.md` (modify) | Add "Run on boot (Raspberry Pi)" section. |

Each script is one focused responsibility. The unit file holds zero logic — just systemd directives. Scripts hold the install logic. Tests are static checks; runtime verification happens manually on the Pi (Task 5).

---

### Task 1: Create the systemd unit file

**Files:**
- Create: `config/systemd/kaizen.service`
- Create: `tests/test_systemd_unit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_systemd_unit.py`:

```python
import unittest
from pathlib import Path


UNIT_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "systemd"
    / "kaizen.service"
)


class SystemdUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = UNIT_PATH.read_text(encoding="utf-8")

    def test_waits_for_network_online(self):
        self.assertIn("After=network-online.target", self.text)
        self.assertIn("Wants=network-online.target", self.text)

    def test_execstart_runs_run_sh_voice(self):
        self.assertIn("ExecStart=%h/kaizen/run.sh --voice", self.text)

    def test_working_directory_is_repo_root(self):
        self.assertIn("WorkingDirectory=%h/kaizen", self.text)

    def test_restart_policy_is_on_failure_with_5s_delay(self):
        self.assertIn("Restart=on-failure", self.text)
        self.assertIn("RestartSec=5", self.text)

    def test_install_target_is_default(self):
        self.assertIn("WantedBy=default.target", self.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/linux/kaizen && .venv/bin/python -m pytest tests/test_systemd_unit.py -v`
Expected: FAIL — `FileNotFoundError: ... kaizen.service`.

- [ ] **Step 3: Create the unit file**

Create `config/systemd/kaizen.service`:

```ini
[Unit]
Description=Kaizen voice assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/kaizen
ExecStart=%h/kaizen/run.sh --voice
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_systemd_unit.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add config/systemd/kaizen.service tests/test_systemd_unit.py
git commit -m "feat(systemd): add kaizen.service user unit"
```

---

### Task 2: Create the install script

**Files:**
- Create: `scripts/install_systemd_service.sh`
- Create: `tests/test_install_systemd_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_install_systemd_service.py`:

```python
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install_systemd_service.sh"


class InstallSystemdServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INSTALL_SH.read_text(encoding="utf-8")

    def test_has_strict_bash_flags(self):
        self.assertIn("set -e", self.text)

    def test_copies_unit_to_user_config_dir(self):
        self.assertIn(".config/systemd/user", self.text)
        self.assertIn("config/systemd/kaizen.service", self.text)

    def test_runs_daemon_reload(self):
        self.assertIn("systemctl --user daemon-reload", self.text)

    def test_enables_and_starts_unit(self):
        self.assertIn("systemctl --user enable --now kaizen.service", self.text)

    def test_checks_and_enables_linger(self):
        self.assertIn("loginctl show-user", self.text)
        self.assertIn("loginctl enable-linger", self.text)

    def test_ensures_persistent_journal_dir(self):
        self.assertIn("/var/log/journal", self.text)

    def test_verifies_wait_online_service(self):
        self.assertIn("NetworkManager-wait-online", self.text)

    def test_refuses_when_run_sh_missing(self):
        self.assertIn("run.sh", self.text)

    def test_is_executable(self):
        self.assertTrue(INSTALL_SH.stat().st_mode & 0o111, "install script not executable")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_install_systemd_service.py -v`
Expected: FAIL — `FileNotFoundError: ... install_systemd_service.sh`.

- [ ] **Step 3: Create the install script**

Create `scripts/install_systemd_service.sh`:

```bash
#!/bin/bash
# install_systemd_service.sh — Install Kaizen as a user systemd service.
#
# Idempotent. Run on the Pi (or any Linux box where you want Kaizen on boot).
#
# What it does:
#   1. Copies config/systemd/kaizen.service -> ~/.config/systemd/user/
#   2. Ensures /var/log/journal exists so logs survive reboot
#   3. Ensures `loginctl enable-linger $USER` so the user manager runs at boot
#   4. systemctl --user daemon-reload && enable --now kaizen.service
#   5. Verifies a wait-online service is enabled (NetworkManager or systemd-networkd)

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_SRC="$REPO_ROOT/config/systemd/kaizen.service"
UNIT_DEST="$HOME/.config/systemd/user/kaizen.service"

[ -f "$REPO_ROOT/run.sh" ] || fail "run.sh not found at $REPO_ROOT/run.sh — wrong repo layout"
[ -f "$UNIT_SRC" ] || fail "unit file not found at $UNIT_SRC"

# 1. Copy unit file
mkdir -p "$HOME/.config/systemd/user"
cp "$UNIT_SRC" "$UNIT_DEST"
ok "installed unit at $UNIT_DEST"

# 2. Persistent journal
if [ ! -d /var/log/journal ]; then
    warn "/var/log/journal missing — logs will not survive reboot. Creating now (sudo)."
    sudo mkdir -p /var/log/journal
    sudo systemctl restart systemd-journald
    ok "persistent journal enabled"
else
    ok "persistent journal already enabled"
fi

# 3. Linger
LINGER="$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)"
if [ "$LINGER" != "yes" ]; then
    warn "linger disabled for $USER — user services won't start at boot. Enabling (sudo)."
    sudo loginctl enable-linger "$USER"
    ok "linger enabled for $USER"
else
    ok "linger already enabled for $USER"
fi

# 4. Reload + enable
systemctl --user daemon-reload
systemctl --user enable --now kaizen.service
ok "kaizen.service enabled and started"

# 5. Verify wait-online service
if systemctl is-enabled NetworkManager-wait-online.service &>/dev/null; then
    ok "NetworkManager-wait-online enabled — network-online.target will gate startup"
elif systemctl is-enabled systemd-networkd-wait-online.service &>/dev/null; then
    ok "systemd-networkd-wait-online enabled — network-online.target will gate startup"
else
    warn "no wait-online service enabled — service may start before network is up"
    warn "fix: sudo systemctl enable NetworkManager-wait-online.service"
fi

echo ""
systemctl --user --no-pager status kaizen.service || true
echo ""
echo "Live logs: journalctl --user -u kaizen -f"
echo "Stop:      systemctl --user stop kaizen"
echo "Disable:   ./scripts/uninstall_systemd_service.sh"
```

- [ ] **Step 4: Make it executable**

```bash
chmod +x scripts/install_systemd_service.sh
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_install_systemd_service.py -v`
Expected: all tests pass.

- [ ] **Step 6: Lint with shellcheck (if available)**

Run: `shellcheck scripts/install_systemd_service.sh`
Expected: no warnings, or skip if shellcheck not installed.

- [ ] **Step 7: Commit**

```bash
git add scripts/install_systemd_service.sh tests/test_install_systemd_service.py
git commit -m "feat(systemd): add idempotent install script for kaizen.service"
```

---

### Task 3: Create the uninstall script

**Files:**
- Create: `scripts/uninstall_systemd_service.sh`
- Modify: `tests/test_install_systemd_service.py` (add uninstall assertions)

- [ ] **Step 1: Add failing tests for the uninstall script**

Append to `tests/test_install_systemd_service.py`:

```python
UNINSTALL_SH = REPO_ROOT / "scripts" / "uninstall_systemd_service.sh"


class UninstallSystemdServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = UNINSTALL_SH.read_text(encoding="utf-8")

    def test_disables_unit(self):
        self.assertIn("systemctl --user disable --now kaizen.service", self.text)

    def test_removes_unit_file(self):
        self.assertIn(".config/systemd/user/kaizen.service", self.text)
        self.assertIn("rm -f", self.text)

    def test_runs_daemon_reload(self):
        self.assertIn("systemctl --user daemon-reload", self.text)

    def test_prompts_before_disabling_linger(self):
        # linger may be load-bearing for other user services; do not auto-disable
        self.assertIn("loginctl disable-linger", self.text)
        self.assertIn("read", self.text)

    def test_is_executable(self):
        self.assertTrue(UNINSTALL_SH.stat().st_mode & 0o111, "uninstall script not executable")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_install_systemd_service.py::UninstallSystemdServiceTests -v`
Expected: FAIL — `FileNotFoundError: ... uninstall_systemd_service.sh`.

- [ ] **Step 3: Create the uninstall script**

Create `scripts/uninstall_systemd_service.sh`:

```bash
#!/bin/bash
# uninstall_systemd_service.sh — Tear down the Kaizen user systemd service.
#
# Symmetric to install_systemd_service.sh. Safe to run if the service was
# never installed (errors are tolerated).

set -uo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

UNIT_DEST="$HOME/.config/systemd/user/kaizen.service"

# 1. Disable + stop (tolerate missing)
if systemctl --user list-unit-files kaizen.service &>/dev/null; then
    systemctl --user disable --now kaizen.service 2>/dev/null || true
    ok "kaizen.service disabled and stopped"
else
    warn "kaizen.service not loaded — nothing to disable"
fi

# 2. Remove unit file
if [ -f "$UNIT_DEST" ]; then
    rm -f "$UNIT_DEST"
    ok "removed $UNIT_DEST"
fi

# 3. Reload
systemctl --user daemon-reload
ok "daemon-reload complete"

# 4. Optional: disable linger (other user services may depend on it)
echo ""
read -r -p "Also disable linger for $USER? Other user services will stop on logout. [y/N] " ans
case "$ans" in
    [yY]|[yY][eE][sS])
        sudo loginctl disable-linger "$USER"
        ok "linger disabled for $USER"
        ;;
    *)
        warn "linger left enabled (recommended)"
        ;;
esac

echo ""
echo "Done. To reinstall: ./scripts/install_systemd_service.sh"
```

- [ ] **Step 4: Make it executable**

```bash
chmod +x scripts/uninstall_systemd_service.sh
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_install_systemd_service.py -v`
Expected: all install + uninstall tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/uninstall_systemd_service.sh tests/test_install_systemd_service.py
git commit -m "feat(systemd): add uninstall script for kaizen.service"
```

---

### Task 4: README "Run on boot" section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the right insertion point**

Run: `grep -n "^##\|^# " README.md`
Identify a sensible location (after the existing setup/usage section, before contributing/license).

- [ ] **Step 2: Add the section**

Insert this section (adjust the heading level to match the README's style):

```markdown
## Run on boot (Raspberry Pi)

To make Kaizen start automatically when the Pi powers on:

```bash
./scripts/install_systemd_service.sh
```

The installer is idempotent — re-run it any time the unit file changes. It will:

- Copy `config/systemd/kaizen.service` to `~/.config/systemd/user/`.
- Enable `loginctl enable-linger` so user services start at boot (asks for sudo).
- Ensure `/var/log/journal` exists so logs survive reboot (asks for sudo).
- Enable + start the service.

### Day-to-day

```bash
systemctl --user status kaizen      # is it running?
systemctl --user restart kaizen     # restart after a config change
systemctl --user stop kaizen        # stop until next boot or manual start
journalctl --user -u kaizen -f      # tail live logs
journalctl --user -u kaizen -p err --since '1 hour ago'   # crashes only
```

### Uninstall

```bash
./scripts/uninstall_systemd_service.sh
```
```

- [ ] **Step 3: Verify Markdown renders**

Run: `cat README.md | head -200` and inspect the new section.
Expected: clean Markdown, no broken headings.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add 'Run on boot' section for systemd auto-start"
```

---

### Task 5: Real-Pi verification (manual)

**Files:** none — this is a hardware checklist.

This task does not produce code. It produces evidence that the previous tasks work end-to-end. Each step is a manual action with an explicit success criterion. Run on the Pi (`ssh pi`).

- [ ] **Step 1: Pull latest on the Pi**

```bash
ssh pi "cd ~/kaizen && git pull"
```

- [ ] **Step 2: Run the installer**

```bash
ssh pi "cd ~/kaizen && ./scripts/install_systemd_service.sh"
```

Expected: all green checkmarks, status shows `active (running)`.

- [ ] **Step 3: Verify the wake word works without a shell session**

From the Pi (or a phone in the same room): say "Jarvis, what time is it?".
Expected: voice response.

- [ ] **Step 4: Reboot and confirm auto-start**

```bash
ssh pi "sudo reboot"
# wait ~30 seconds, then:
ssh pi "systemctl --user status kaizen --no-pager"
```

Expected: `active (running)`, with no manual start needed.

After SSH is back, say the wake word again. Expected: response.

- [ ] **Step 5: Verify crash logging**

Force a crash by killing the python process:

```bash
ssh pi "pkill -9 -f 'python.*main.py'"
sleep 6
ssh pi "systemctl --user status kaizen --no-pager"
ssh pi "journalctl --user -u kaizen --since '1 minute ago' | tail -30"
```

Expected: status shows `active (running)` again (auto-restarted after 5s); journal shows the kill and the restart.

- [ ] **Step 6: Verify clean stop is honored**

```bash
ssh pi "systemctl --user stop kaizen"
sleep 10
ssh pi "systemctl --user status kaizen --no-pager"
```

Expected: status `inactive (dead)`, did NOT auto-restart (because Restart=on-failure, not always).

Restart it manually:

```bash
ssh pi "systemctl --user start kaizen"
```

- [ ] **Step 7: Document the result**

If everything passed, no action — the feature is shipped. If anything failed, capture the journal output and open an issue / save a project memory describing the failure mode for follow-up.

---

## Done criteria

- All static-content tests pass: `.venv/bin/python -m pytest tests/test_systemd_unit.py tests/test_install_systemd_service.py -v`.
- Existing test suite still green: `.venv/bin/python -m pytest -x`.
- Pi reboots → Kaizen responds to the wake word with no shell intervention (Task 5, Step 4).
- A forced kill triggers a 5s restart and a journal entry (Task 5, Step 5).
- A `systemctl --user stop` is honored and does not auto-resurrect (Task 5, Step 6).
