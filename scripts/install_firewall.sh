#!/usr/bin/env bash
# Install Kaizen's host firewall (nftables) on the Pi.
#
#   - Default-deny inbound; SSH (22), Spotify Connect discovery (4070) and
#     mDNS allowed from the home network only.
#   - Skill containers can reach the internet but not the Pi's own services
#     or other devices on the home network.
#   - Pins librespot's discovery port to 4070 (raspotify drop-in).
#
# Safety: the rules are applied live with an automatic rollback in 120s.
# Check SSH still works from another terminal, then confirm to keep them.
# Uninstall: sudo systemctl disable --now kaizen-firewall.service
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/config/firewall"

if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

install -D -m 0644 "$SRC/kaizen-firewall.nft" /etc/kaizen/kaizen-firewall.nft
install -m 0644 "$SRC/kaizen-firewall.service" /etc/systemd/system/kaizen-firewall.service
if systemctl list-unit-files raspotify.service &>/dev/null; then
    install -D -m 0644 "$SRC/raspotify-zeroconf-port.conf" \
        /etc/systemd/system/raspotify.service.d/zeroconf-port.conf
fi
systemctl daemon-reload
systemctl try-restart raspotify.service || true

/usr/sbin/nft -f /etc/kaizen/kaizen-firewall.nft
systemd-run --quiet --unit=kaizen-firewall-rollback --on-active=120 \
    /usr/sbin/nft delete table inet kaizen_firewall
echo "Firewall applied. It will be removed automatically in 120s."
echo "From another terminal, check SSH still works (e.g. ssh pi true)."

if read -r -t 110 -p "Keep the firewall? [y/N] " answer && [[ "$answer" =~ ^[Yy] ]]; then
    systemctl stop kaizen-firewall-rollback.timer 2>/dev/null || true
    systemctl enable --quiet kaizen-firewall.service
    systemctl start kaizen-firewall.service
    echo "Kept, and enabled at boot (kaizen-firewall.service)."
else
    echo
    echo "Not confirmed — the rollback timer will remove the rules."
fi
