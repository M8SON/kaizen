"""Read and write Kaizen's .env for the panel.

Only allowlisted keys can be written. Writes are atomic (temp file, fsync,
rename) with mode 0600, and each write first keeps a 0600 backup
(.env.bak.<timestamp>), pruned to the newest five. Secret values never leave
this module except as a mask ("ends in 1234").
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

# Keys: never sent to the browser.
SECRET_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "TYPESAFE_API_KEY",
    "META_MODEL_API_KEY",
    "BRAVE_API_KEY",
    "OPENWEATHER_API_KEY",
})

# Plain settings the panel may change; value -> validator.
_NUM = re.compile(r"^\d+(\.\d+)?$")
SETTINGS = {
    "CLAUDE_MODEL": lambda v: bool(re.fullmatch(r"claude-[a-z0-9.-]+", v)),
    "FAST_MODEL": lambda v: v == "" or bool(re.fullmatch(r"claude-[a-z0-9.-]+", v)),
    "TTS_BACKEND": lambda v: v in ("elevenlabs", "kokoro-onnx"),
    "ELEVENLABS_VOICE_ID": lambda v: bool(re.fullmatch(r"[A-Za-z0-9]{10,40}", v)),
    "TTS_SPEED": lambda v: bool(_NUM.match(v)) and 0.5 <= float(v) <= 2.0,
    "FILLER_CLASSIFIER_ENABLED": lambda v: v in ("true", "false"),
    "FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD": lambda v: bool(_NUM.match(v)) and 0 <= float(v) <= 1,
    "STT_BACKEND": lambda v: v in ("meta", "whisper"),
    "WAKE_WORD_THRESHOLD": lambda v: bool(_NUM.match(v)) and 0.3 <= float(v) <= 0.8,
    "WAKE_WORD_SOFT_THRESHOLD": lambda v: bool(_NUM.match(v)) and 0 <= float(v) <= 0.8,
    "OWNER_NAME": lambda v: len(v) <= 60,
    "HOME_LOCATION": lambda v: len(v) <= 100,
}

ALLOWED = SECRET_KEYS | frozenset(SETTINGS)
KEEP_BACKUPS = 5

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class EnvError(ValueError):
    pass


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    if " #" in raw:  # inline comment on an unquoted value
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#"):
            values[m.group(1)] = _unquote(m.group(2))
    return values


def mask(value: str | None) -> str:
    """"not set", or "set, ends in 1234" (never more than 4 characters)."""
    if not value:
        return "not set"
    if len(value) < 12:
        return "set"
    return f"set, ends in {value[-4:]}"


def validate(key: str, value: str) -> None:
    if key not in ALLOWED:
        raise EnvError(f"{key} can't be changed from the panel")
    if "\n" in value or "\r" in value or "\0" in value:
        raise EnvError("value can't contain a line break")
    if key in SECRET_KEYS:
        if not value or len(value) > 400 or any(c.isspace() for c in value):
            raise EnvError("that doesn't look like a key")
    elif not SETTINGS[key](value):
        raise EnvError(f"{value!r} isn't a valid value for {key}")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak.{stamp}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.bak.{stamp}-{n}")
        n += 1
    _atomic_write(dest, path.read_text(encoding="utf-8"))
    # Prune only the panel's own backups (timestamped names), newest kept.
    ours = sorted(p for p in path.parent.glob(f"{path.name}.bak.*")
                  if re.fullmatch(rf"{re.escape(path.name)}\.bak\.\d{{8}}-\d{{6}}(-\d+)?", p.name))
    for old in ours[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Set each key (validated) in .env, keeping every other line as it is."""
    for key, value in updates.items():
        validate(key, value)
    if not updates:
        return
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out = []
    for line in lines:
        m = _LINE.match(line)
        if m and not line.lstrip().startswith("#") and m.group(1) in pending:
            key = m.group(1)
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in pending.items())
    _backup(path)
    _atomic_write(path, "\n".join(out) + "\n")


def scrub(text: str, secrets) -> str:
    """Replace every secret value that appears in a message with [key]."""
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "[key]")
    return text
