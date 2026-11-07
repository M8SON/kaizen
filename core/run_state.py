"""Kaizen's coarse run state for the control panel: "busy" (starting up,
speaking, in a conversation) or "idle" (waiting for the wake word).

Written to ~/.kaizen/run/state as `<state> <pid>`; the panel restarts Kaizen
after a Save only when the state is idle. Never raises.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_path() -> Path:
    return Path(os.environ.get("KAIZEN_PANEL_DIR", Path.home() / ".kaizen")) / "run" / "state"


def write(state: str) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("state.tmp")
        tmp.write_text(f"{state} {os.getpid()}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def read() -> tuple[str, int | None]:
    """(state, pid); ("unknown", None) when there is no readable file."""
    try:
        state, pid = state_path().read_text().split()
        return state, int(pid)
    except (OSError, ValueError):
        return "unknown", None
