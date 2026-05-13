#!/bin/bash
# uninstall_systemd_service.sh — Tear down the Kaizen user systemd service.
#
# Symmetric to install_systemd_service.sh. Safe to run if the service was
# never installed (errors are tolerated).

# `-e` intentionally omitted: this script tolerates errors from systemctl when
# the unit was never installed (disable on a missing unit, daemon-reload when
# the user manager isn't running). `-uo pipefail` still catches typos.
set -uo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

UNIT_DEST="$HOME/.config/systemd/user/kaizen.service"

# 1. Disable + stop (tolerate missing — `|| true` swallows the "not loaded" error)
systemctl --user disable --now kaizen.service 2>/dev/null || true
ok "kaizen.service disabled and stopped (or already absent)"

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
