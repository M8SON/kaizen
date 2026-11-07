"""Self-signed certificate for the panel, made and kept current automatically.

On every start: if there is no certificate, or it doesn't cover the Pi's
current hostname and LAN addresses, make a new one with openssl (EC P-256,
10 years). Otherwise keep it, so browsers that already accepted it don't
warn again.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import ssl
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CERT = "panel.crt"
KEY = "panel.key"
NAMES = "names.txt"  # the subjectAltNames the current cert was made for


def lan_addresses() -> list[str]:
    """IPv4 addresses on real interfaces (no loopback, Docker or veth)."""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    addrs = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, cidr = parts[1], parts[3]
        if iface == "lo" or iface.startswith(("docker", "br-", "veth", "virbr")):
            continue
        addrs.append(cidr.split("/")[0])
    return sorted(set(addrs))


def wanted_names(hostname: str | None = None, addresses: list[str] | None = None) -> list[str]:
    host = (hostname or socket.gethostname()).split(".")[0]
    names = [f"DNS:{host}.local", f"DNS:{host}", "DNS:localhost", "IP:127.0.0.1"]
    for a in (lan_addresses() if addresses is None else addresses):
        ipaddress.ip_address(a)  # never pass anything else to openssl
        names.append(f"IP:{a}")
    return names


def ensure_cert(tls_dir: Path, hostname: str | None = None,
                addresses: list[str] | None = None) -> tuple[Path, Path, bool]:
    """Return (cert, key, made_new)."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tls_dir, 0o700)
    cert, key, names_file = tls_dir / CERT, tls_dir / KEY, tls_dir / NAMES
    names = wanted_names(hostname, addresses)
    san = ",".join(names)
    if cert.exists() and key.exists() and names_file.exists() \
            and names_file.read_text().strip() == san:
        return cert, key, False

    host = names[0].split(":", 1)[1]
    tmp_key, tmp_cert = tls_dir / (KEY + ".new"), tls_dir / (CERT + ".new")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-nodes", "-days", "3650", "-keyout", str(tmp_key), "-out", str(tmp_cert),
         "-subj", f"/CN={host}/O=Kaizen control panel", "-addext", f"subjectAltName={san}",
         "-addext", "basicConstraints=critical,CA:FALSE",
         "-addext", "extendedKeyUsage=serverAuth"],
        check=True, capture_output=True, timeout=60,
    )
    os.chmod(tmp_key, 0o600)
    os.replace(tmp_key, key)
    os.replace(tmp_cert, cert)
    names_file.write_text(san + "\n")
    logger.info("Panel: new TLS certificate for %s", san)
    return cert, key, True


def fingerprint(cert: Path) -> str:
    """SHA-256 fingerprint as browsers show it (AB:CD:...)."""
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
