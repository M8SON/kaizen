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
echo "Start:     systemctl --user start kaizen"
echo "Stop:      systemctl --user stop kaizen"
echo "Disable:   ./scripts/uninstall_systemd_service.sh"
