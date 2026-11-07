"""The panel's own state in ~/.kaizen/panel.json (mode 0600): the admin
password's scrypt hash, the session-signing secret and, while no password is
set, the six-digit setup code Jarvis speaks.

Also read by Kaizen (main.py) to speak that code at startup, so this module
imports nothing but the standard library.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path

PANEL_HTTP_PORT = 8080
PANEL_HTTPS_PORT = 8443

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def data_dir() -> Path:
    return Path(os.environ.get("KAIZEN_PANEL_DIR", Path.home() / ".kaizen"))


def state_path() -> Path:
    return data_dir() / "panel.json"


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".panel.json.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load(path: Path | None = None) -> dict:
    """Read the state, creating the session secret (and, while no password
    is set, the setup code) on first use."""
    path = path or state_path()
    with _locked(path):
        data = _load(path)
        changed = False
        if not data.get("session_secret"):
            data["session_secret"] = secrets.token_hex(32)
            changed = True
        if not data.get("password_hash") and not data.get("setup_code"):
            data["setup_code"] = f"{secrets.randbelow(10 ** 6):06d}"
            changed = True
        if changed:
            _save(path, data)
        return data


def pending_setup_code(path: Path | None = None) -> str | None:
    """The setup code while the panel has no password yet, else None.
    Read-only: the file exists only once the panel has started, so an
    install without the panel never hears a code."""
    data = _load(path or state_path())
    return None if data.get("password_hash") else data.get("setup_code")


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def set_password(password: str, path: Path | None = None) -> None:
    path = path or state_path()
    salt = secrets.token_bytes(16)
    with _locked(path):
        data = _load(path)
        data["password_hash"] = _hash(password, salt).hex()
        data["password_salt"] = salt.hex()
        data["session_secret"] = secrets.token_hex(32)  # log out old sessions
        data.pop("setup_code", None)
        _save(path, data)


def check_password(password: str, path: Path | None = None) -> bool:
    data = load(path)
    if not data.get("password_hash"):
        return False
    got = _hash(password, bytes.fromhex(data["password_salt"]))
    return hmac.compare_digest(got.hex(), data["password_hash"])


def spoken_address() -> str:
    """How Jarvis says the panel address, e.g. "daedalus dot local, port 8080"
    (the plain-HTTP port, which redirects to HTTPS)."""
    host = socket.gethostname().split(".")[0]
    return f"{host} dot local, port {PANEL_HTTP_PORT}"


def spoken_code(code: str) -> str:
    """"4 1 2, 9 0 3" so TTS reads digits, not a number."""
    return " ".join(code[:3]) + ", " + " ".join(code[3:])
