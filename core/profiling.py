"""
Per-turn stage timing for the voice pipeline.

Gated by MINICLAW_PROFILE=true. When disabled, both context managers are
true no-ops. When enabled, each turn emits a single INFO log line:

    [TIMING-SUMMARY] stt=412 tier=3 llm_ollama=3540 tool_weather=1280 total=7625
"""

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_enabled = False
_current_turn: ContextVar["dict[str, int] | None"] = ContextVar(
    "current_turn", default=None
)


def _refresh_enabled() -> None:
    """Re-read MINICLAW_PROFILE. Called at import time and exposed so
    tests can flip the flag via monkeypatch."""
    global _enabled
    _enabled = os.environ.get("MINICLAW_PROFILE", "").strip().lower() == "true"


_refresh_enabled()


@contextmanager
def turn():
    """Open a per-turn timing scope. Emits the summary on exit."""
    if not _enabled:
        yield None
        return
    stages: dict[str, int] = {}
    token = _current_turn.set(stages)
    try:
        yield stages
    finally:
        _current_turn.reset(token)
        if stages:
            line = " ".join(f"{k}={v}" for k, v in stages.items())
            logger.info("[TIMING-SUMMARY] %s", line)


@contextmanager
def stage(name: str):
    """Time a stage. No-op when disabled or no turn() is open."""
    if not _enabled:
        yield
        return
    stages = _current_turn.get()
    if stages is None:
        yield
        return
    final = name
    n = 1
    while final in stages:
        n += 1
        final = f"{name}_{n}"
    t0 = time.perf_counter()
    try:
        yield
    finally:
        stages[final] = int((time.perf_counter() - t0) * 1000)
