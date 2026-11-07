"""Status page checks and the idle-aware restart.

Every command is a fixed argument list: no shell, and no user input in any
argument.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time

from core import run_state

logger = logging.getLogger(__name__)

UNIT = "kaizen.service"


def _run(args: list[str], timeout: float = 5) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def jarvis() -> dict:
    props = _run(["systemctl", "--user", "show", UNIT, "-p", "ActiveState",
                  "-p", "ActiveEnterTimestamp", "-p", "NRestarts", "-p", "MainPID"])
    info = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
    state, pid = run_state.read()
    main_pid = int(info.get("MainPID") or 0)
    return {
        "active": info.get("ActiveState", "unknown"),
        "since": info.get("ActiveEnterTimestamp", ""),
        "restarts": info.get("NRestarts", ""),
        # A state left by a process that has since died says nothing.
        "state": state if pid and _alive(pid) else "unknown",
        "main_pid": main_pid,
    }


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def speaker() -> dict:
    out = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    name = re.search(r'node\.name = "(.*)"', out)
    desc = re.search(r'node\.description = "(.*)"', out)
    name = name.group(1) if name else ""
    return {"ok": bool(name) and name != "auto_null",
            "name": desc.group(1) if desc else (name or "none"),
            "dummy": name == "auto_null"}


def microphone() -> dict:
    try:
        cards = open("/proc/asound/cards").read()
    except OSError:
        cards = ""
    usb = [line.split("]:", 1)[1].strip() for line in cards.splitlines()
           if "]:" in line and "USB-Audio" in line]
    return {"ok": bool(usb), "name": usb[0] if usb else "no USB microphone"}


def network() -> dict:
    route = _run(["ip", "-4", "route", "show", "default"])
    m = re.search(r"dev (\S+)", route)
    iface = m.group(1) if m else ""
    ssid = ""
    if iface.startswith("wl"):
        ssid = _run(["iwgetid", "-r"]).strip()
    addr = re.search(r"inet (\S+)/", _run(["ip", "-4", "addr", "show", "dev", iface])) if iface else None
    return {"iface": iface or "none", "ssid": ssid, "ip": addr.group(1) if addr else ""}


_WAKE = re.compile(r"^(\S+) \[core\.voice\] INFO: (?:"
                   r"Wake detected \((?:(soft), )?score ([0-9.]+)\)"
                   r"|Wake near-miss: peak score ch0=([0-9.]+) ch1=([0-9.]+))")


def wake_scores(limit: int = 10) -> list[dict]:
    """The last wake events from the journal: time, peak score, outcome."""
    out = _run(["journalctl", "--no-pager", "-o", "cat", "-n", "20000",
                f"_SYSTEMD_USER_UNIT={UNIT}"], timeout=10)
    events = []
    for line in out.splitlines():
        m = _WAKE.match(line)
        if not m:
            continue
        when, soft, score, ch0, ch1 = m.groups()
        if score is not None:
            events.append({"time": when, "score": float(score),
                           "outcome": "soft wake" if soft else "woke"})
        else:
            events.append({"time": when, "score": max(float(ch0), float(ch1)),
                           "outcome": "missed"})
    return events[-limit:][::-1]


class Restarter:
    """Restart Kaizen once it's idle, coalescing repeated requests."""

    def __init__(self, poll_s: float = 1.0, run=None):
        self._poll_s = poll_s
        self._run = run or (lambda: subprocess.run(
            ["systemctl", "--user", "restart", UNIT], timeout=60, check=False))
        self._pending = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_restart: float | None = None

    @property
    def pending(self) -> bool:
        return self._pending

    def request(self, now: bool = False) -> str:
        """"restarted" or "waiting" (for the current conversation to end)."""
        with self._lock:
            if now or self._can_restart():
                self._pending = False
                self._restart()
                return "restarted"
            self._pending = True
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._wait, daemon=True,
                                                name="panel-restart")
                self._thread.start()
            return "waiting"

    def _can_restart(self) -> bool:
        info = jarvis()
        # Not running, or no live state file: nothing to interrupt.
        return info["active"] != "active" or info["state"] in ("idle", "unknown")

    def _restart(self) -> None:
        logger.info("Panel: restarting %s", UNIT)
        self._run()
        self.last_restart = time.time()

    def _wait(self) -> None:
        while True:
            time.sleep(self._poll_s)
            with self._lock:
                if not self._pending:
                    return
                if self._can_restart():
                    self._pending = False
                    self._restart()
                    return
