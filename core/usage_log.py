"""
Usage log: one row per billed API call (Claude, Jev, ElevenLabs, Meta STT).

Each row stores what the service itself reported for that call:
  claude      response.usage (input, output, cache read, cache write tokens)
  jev         response.usage (input, output tokens)
  elevenlabs  the `character-cost` response header (credits)
  meta_stt    `audioProcessedMs` on the final transcript (audio_ms)

Prices live in config/pricing.yaml and are applied at report time
(scripts/cost_report.py), so a price change never rewrites history.

Fail-open like the session archive: a sqlite error is logged once and the
call goes on. Path: USAGE_LOG_PATH (default ~/.kaizen/usage.db);
USAGE_LOG_ENABLED=false turns it off.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    service TEXT NOT NULL,
    model TEXT,
    purpose TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    credits REAL NOT NULL DEFAULT 0,
    audio_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
"""

_lock = threading.Lock()
_ready_path: Path | None = None
_warned = False


def db_path() -> Path:
    return Path(os.environ.get("USAGE_LOG_PATH", Path.home() / ".kaizen" / "usage.db"))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record(service: str, model: str | None = None, purpose: str | None = None, *,
           input_tokens=0, output_tokens=0, cache_read_tokens=0,
           cache_write_tokens=0, credits=0, audio_ms=0) -> None:
    """Append one usage row. Never raises."""
    global _ready_path, _warned
    if os.environ.get("USAGE_LOG_ENABLED", "true").strip().lower() == "false":
        return
    try:
        credits = float(credits or 0)
    except (TypeError, ValueError):
        credits = 0.0
    path = db_path()
    try:
        with _lock:
            conn = sqlite3.connect(path, timeout=2.0)
            try:
                if _ready_path != path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    conn.executescript(_SCHEMA)
                    _ready_path = path
                conn.execute(
                    "INSERT INTO usage (ts, service, model, purpose, input_tokens,"
                    " output_tokens, cache_read_tokens, cache_write_tokens, credits,"
                    " audio_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), service, model, purpose, _int(input_tokens),
                     _int(output_tokens), _int(cache_read_tokens),
                     _int(cache_write_tokens), credits, _int(audio_ms)),
                )
                conn.commit()
            finally:
                conn.close()
    except (sqlite3.Error, OSError) as exc:
        if not _warned:
            logger.warning("Usage log write failed (%s); continuing without it", exc)
            _warned = True


def record_claude(model: str, usage, purpose: str | None = None) -> None:
    """Record an Anthropic `response.usage` object."""
    if usage is None:
        return
    record(
        "claude", model, purpose,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0),
    )
