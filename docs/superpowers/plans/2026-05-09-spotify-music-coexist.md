# Spotify + SoundCloud Music Coexistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Spotify as the default music backend alongside SoundCloud (kept for remixes/DJ content); transport commands fan through a small `music-control` router that dispatches to whichever backend is active.

**Architecture:** Three skills (`spotify`, `soundcloud`, `music-control`) coordinate via `ContainerManager._active_music_source`. Spotify Web API is reached through `spotipy`; audio plays via `librespot` (Spotify Connect speaker daemon on the Pi). OAuth refresh tokens cached at `~/.miniclaw/spotify-tokens.json`. SoundCloud unchanged except its `play` action now calls `_stop_all_music()` for mutual exclusion.

**Tech Stack:** Python 3.13 venv, `spotipy>=2.24` (PyPI), Spotify Web API, `librespot` (Pi-side daemon, not in repo), `difflib` (stdlib, fuzzy match), existing `core/container_manager.py` native-skill plumbing.

**Spec:** `docs/superpowers/specs/2026-05-09-spotify-music-coexist-design.md` (commit `6ae8393`).

---

## File Map

**Create:**
- `core/spotify_auth.py` — token cache + spotipy client factory
- `scripts/spotify_login.py` — one-time OAuth dance
- `skills/spotify/SKILL.md` + `skills/spotify/config.yaml` — Spotify skill manifest
- `skills/music-control/SKILL.md` + `skills/music-control/config.yaml` — transport router skill manifest
- `tests/test_spotify_auth.py` — auth helper unit tests
- `tests/test_spotify_skill.py` — Spotify handler unit tests
- `tests/test_music_control.py` — transport router unit tests

**Modify:**
- `requirements.txt` — add `spotipy>=2.24`
- `core/container_manager.py` — `_active_music_source` field, `_stop_all_music()`, `_stop_spotify_playback()`, `_execute_spotify`, `_execute_music_control`, `_spotify_device_id`, register two new native handlers; light edit to `_execute_soundcloud` to call `_stop_all_music()` first
- `config/intent_patterns.yaml` — repoint 6 transport patterns from `soundcloud` → `music-control`
- `tests/test_soundcloud_handler.py` — extend with one assertion that play calls `_stop_all_music()`
- `tests/test_tier_router.py` — update dispatch tests for transport patterns now routing to music-control
- `.env.example` — document `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`

---

## Task 1: Add `spotipy` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append to `requirements.txt`**

```
# Spotify Web API client (used by core/spotify_auth.py + skills/spotify/)
spotipy>=2.24
```

- [ ] **Step 2: Install in the dev venv**

Run: `.venv/bin/python -m pip install "spotipy>=2.24"`
Expected: `Successfully installed spotipy-2.24.x` (or higher).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat(deps): add spotipy for Spotify Web API"
```

---

## Task 2: `core/spotify_auth.py` — env-var error path (TDD)

**Files:**
- Create: `tests/test_spotify_auth.py`
- Create: `core/spotify_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_spotify_auth.py`:

```python
"""Tests for core.spotify_auth — token cache + client factory."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class GetSpotifyClientErrorPaths(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_raises_when_client_id_missing(self):
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing

        with self.assertRaises(SpotifyAuthMissing) as ctx:
            get_spotify_client()
        self.assertIn("SPOTIFY_CLIENT_ID", str(ctx.exception))

    @patch.dict("os.environ", {"SPOTIFY_CLIENT_ID": "x", "SPOTIFY_CLIENT_SECRET": "y"}, clear=True)
    def test_raises_when_token_cache_missing(self):
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing, TOKEN_CACHE_PATH

        with patch.object(Path, "exists", return_value=False):
            with self.assertRaises(SpotifyAuthMissing) as ctx:
                get_spotify_client()
        self.assertIn("scripts/spotify_login.py", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotify_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.spotify_auth'`.

- [ ] **Step 3: Create `core/spotify_auth.py`**

```python
"""Spotify OAuth token cache + client factory.

Uses spotipy's SpotifyOAuth with a file-backed cache handler so the
refresh token (set up once via scripts/spotify_login.py) is read on
every call and auto-refreshed by spotipy as needed.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_CACHE_PATH = Path.home() / ".miniclaw" / "spotify-tokens.json"

DEFAULT_REDIRECT_URI = "http://localhost:8888/callback"

# Minimum scopes the spotify skill needs:
#   user-modify-playback-state — start/pause/skip/queue
#   user-read-playback-state   — read current device + status
#   user-read-private          — required by some endpoints
#   playlist-read-private      — list user's saved playlists
#   user-library-read          — read liked tracks (used by play_playlist fallback)
SCOPES = " ".join([
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-private",
    "playlist-read-private",
    "user-library-read",
])


class SpotifyAuthMissing(Exception):
    """Raised when env vars or token cache aren't set up yet.

    Callers should catch this and surface the message as a friendly tool
    result — Claude reads it back to the user as a setup hint."""


def get_spotify_client():
    """Return a spotipy.Spotify instance using the cached refresh token.

    Raises SpotifyAuthMissing if env vars or the token file are missing.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    if not client_id or not client_secret:
        raise SpotifyAuthMissing(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env"
        )
    if not TOKEN_CACHE_PATH.exists():
        raise SpotifyAuthMissing(
            f"Spotify token cache not found at {TOKEN_CACHE_PATH}. "
            "Run scripts/spotify_login.py once to authorize."
        )

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth, CacheFileHandler

    cache_handler = CacheFileHandler(cache_path=str(TOKEN_CACHE_PATH))
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler,
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=oauth)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_spotify_auth.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add core/spotify_auth.py tests/test_spotify_auth.py
git commit -m "feat(spotify): auth helper with env-var + token-cache gates"
```

---

## Task 3: `core/spotify_auth.py` — happy path returns spotipy client (TDD)

**Files:**
- Modify: `tests/test_spotify_auth.py`

- [ ] **Step 1: Append the happy-path test**

Add to `tests/test_spotify_auth.py`:

```python
class GetSpotifyClientHappyPath(unittest.TestCase):
    @patch.dict("os.environ", {
        "SPOTIFY_CLIENT_ID": "abc",
        "SPOTIFY_CLIENT_SECRET": "def",
        "SPOTIFY_REDIRECT_URI": "http://localhost:8888/callback",
    }, clear=True)
    def test_returns_spotipy_client_when_all_set(self):
        from core import spotify_auth

        with patch.object(Path, "exists", return_value=True), \
             patch("spotipy.oauth2.SpotifyOAuth") as mock_oauth_cls, \
             patch("spotipy.Spotify") as mock_spotify_cls:
            mock_oauth = MagicMock()
            mock_oauth_cls.return_value = mock_oauth
            mock_client = MagicMock()
            mock_spotify_cls.return_value = mock_client

            result = spotify_auth.get_spotify_client()

        self.assertIs(result, mock_client)
        # OAuth is constructed with the configured creds + scopes
        kwargs = mock_oauth_cls.call_args.kwargs
        self.assertEqual(kwargs["client_id"], "abc")
        self.assertEqual(kwargs["client_secret"], "def")
        self.assertEqual(kwargs["redirect_uri"], "http://localhost:8888/callback")
        self.assertIn("user-modify-playback-state", kwargs["scope"])
        # spotipy.Spotify is wired to the OAuth manager
        mock_spotify_cls.assert_called_once_with(auth_manager=mock_oauth)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_spotify_auth.py -v`
Expected: 3 passed (2 from Task 2 + 1 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_spotify_auth.py
git commit -m "test(spotify): cover get_spotify_client happy path"
```

---

## Task 4: `scripts/spotify_login.py` — one-time OAuth dance

**Files:**
- Create: `scripts/spotify_login.py`

This script is interactive and not unit-tested — verify by running it manually as part of the Pi smoke test in Task 14.

- [ ] **Step 1: Create `scripts/spotify_login.py`**

```python
#!/usr/bin/env python3
"""One-time browser-based OAuth dance to authorize MiniClaw with Spotify.

Setup steps:
  1. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env (or export
     them before running this script).
  2. Make sure http://localhost:8888/callback is registered as a Redirect
     URI in your Spotify dev app settings.
  3. Run: python scripts/spotify_login.py
  4. Open the printed URL in any browser, approve consent.
  5. Your browser will redirect to a localhost URL that may show a
     "connection refused" error — that's expected. Copy the FULL URL
     (including ?code=…) and paste it back when prompted.

After that, the refresh token is cached at ~/.miniclaw/spotify-tokens.json
and MiniClaw uses it forever (or until you revoke the app at
https://www.spotify.com/account/apps/).
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

# Load .env so SPOTIFY_* env vars are picked up
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional for this script; env vars can be exported manually

# Make core/ importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.spotify_auth import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    SCOPES,
    TOKEN_CACHE_PATH,
)


def main() -> int:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    if not client_id or not client_secret:
        print(
            "ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in "
            ".env (or exported in the shell) before running this script.",
            file=sys.stderr,
        )
        return 1

    from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_handler = CacheFileHandler(cache_path=str(TOKEN_CACHE_PATH))
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler,
        open_browser=False,
    )

    print("\n=== Spotify OAuth setup ===\n")
    print("1. Open this URL in your browser:\n")
    print(f"   {oauth.get_authorize_url()}\n")
    print("2. Approve consent. Your browser will redirect to a localhost URL")
    print(f"   ({redirect_uri}?code=...). It may show a connection error — that's fine.\n")
    print("3. Paste the FULL redirected URL here (it has ?code=... in the query string):\n")

    try:
        response_url = input("Redirected URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(response_url)
    code_values = urllib.parse.parse_qs(parsed.query).get("code")
    if not code_values:
        print(
            "\nERROR: couldn't find ?code=... in the URL you pasted. Make sure "
            "you copy the URL after the redirect, not before.",
            file=sys.stderr,
        )
        return 1
    code = code_values[0]

    token_info = oauth.get_access_token(code, as_dict=True, check_cache=False)
    print(f"\n✓ Token cached at {TOKEN_CACHE_PATH}")
    print(f"  Access token expires in {token_info.get('expires_in', '?')} seconds.")
    print("  Refresh token saved (auto-refreshed forever).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/spotify_login.py`

- [ ] **Step 3: Smoke-test the entry path locally (no real auth)**

Run: `.venv/bin/python scripts/spotify_login.py < /dev/null`
Expected: prints `ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set...` and exits non-zero. (We're verifying the script imports + runs the env-var check, not doing real OAuth.)

- [ ] **Step 4: Commit**

```bash
git add scripts/spotify_login.py
git commit -m "feat(spotify): one-time OAuth setup script"
```

---

## Task 5: `ContainerManager._active_music_source` + `_stop_all_music()` (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Create: `tests/test_music_control.py` (skeleton — tests for state field)

- [ ] **Step 1: Write the failing test**

Create `tests/test_music_control.py`:

```python
"""Tests for ContainerManager._active_music_source and _stop_all_music."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_manager():
    from core.container_manager import ContainerManager
    return ContainerManager()


class ActiveMusicSourceState(unittest.TestCase):
    def test_initial_state_is_none(self):
        m = _make_manager()
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_is_idempotent_when_nothing_playing(self):
        m = _make_manager()
        m._stop_all_music()  # must not raise
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_clears_active_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        with patch.object(m, "_stop_mpv"), \
             patch.object(m, "_stop_spotify_playback"):
            m._stop_all_music()
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_calls_both_backends(self):
        m = _make_manager()
        with patch.object(m, "_stop_mpv") as mock_mpv, \
             patch.object(m, "_stop_spotify_playback") as mock_spot:
            m._stop_all_music()
        mock_mpv.assert_called_once()
        mock_spot.assert_called_once()

    def test_stop_all_music_swallows_backend_exceptions(self):
        m = _make_manager()
        with patch.object(m, "_stop_mpv", side_effect=RuntimeError("boom")), \
             patch.object(m, "_stop_spotify_playback", side_effect=RuntimeError("boom2")):
            m._stop_all_music()  # must not raise
        self.assertIsNone(m._active_music_source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: 5 FAILs (`AttributeError: 'ContainerManager' object has no attribute '_active_music_source'` / `_stop_all_music` / `_stop_spotify_playback`).

- [ ] **Step 3: Add the field, helper methods, and the stub**

In `core/container_manager.py`:

Find `__init__` (around line 45) and add the new field next to the existing `_mpv_*` ones:

```python
        self._mpv_process: subprocess.Popen | None = None
        self._mpv_socket_path: str = "/tmp/miniclaw-mpv.sock"
        self._mpv_log_path: Path = Path.home() / ".miniclaw" / "mpv.log"
        self._mpv_log_fh = None
        # Mutual-exclusion: only one music backend plays at a time.
        # Set by play actions, cleared by _stop_all_music. Read by
        # _execute_music_control to dispatch transport commands.
        self._active_music_source: str | None = None
```

After `_stop_mpv` (find it near the end of the file), add:

```python
    def _stop_all_music(self) -> None:
        """Stop any currently-playing music regardless of source.

        Called by every play action (in any music skill) and the
        music-control skill's stop action. Idempotent and exception-safe:
        each backend's stop helper has its own try/except so one failing
        won't block the other from running.
        """
        try:
            self._stop_mpv()
        except Exception:
            logger.exception("_stop_mpv failed during _stop_all_music")
        try:
            self._stop_spotify_playback()
        except Exception:
            logger.exception("_stop_spotify_playback failed during _stop_all_music")
        self._active_music_source = None

    def _stop_spotify_playback(self) -> None:
        """Stub — filled in by Task 11 once the Spotify backend exists.

        Splitting this out lets _stop_all_music ship before _execute_spotify,
        keeping each task small and testable on its own.
        """
        return
```

- [ ] **Step 4: Run test to verify all five pass**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_music_control.py
git commit -m "feat(music): _active_music_source state + _stop_all_music"
```

---

## Task 6: `_execute_music_control` handler (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Modify: `tests/test_music_control.py`

- [ ] **Step 1: Append the failing tests**

Add to `tests/test_music_control.py`:

```python
class MusicControlDispatch(unittest.TestCase):
    def test_no_active_source_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")

    def test_unknown_action_is_rejected(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        result = m._execute_music_control({"action": "explode"})
        self.assertIn("unknown", result.lower())

    def test_soundcloud_skip_calls_mpv_playlist_next(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Skipped.") as mock_act:
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Skipped.")
        mock_act.assert_called_once_with(["playlist-next"], "Skipped.")

    def test_soundcloud_stop_calls_stop_mpv(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_stop_mpv", return_value="Stopped.") as mock_stop:
            result = m._execute_music_control({"action": "stop"})
        self.assertEqual(result, "Stopped.")
        mock_stop.assert_called_once()

    def test_soundcloud_pause_calls_mpv_set_property(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Paused.") as mock_act:
            m._execute_music_control({"action": "pause"})
        mock_act.assert_called_once_with(["set_property", "pause", True], "Paused.")

    def test_soundcloud_resume_calls_mpv_unpause(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Resumed.") as mock_act:
            m._execute_music_control({"action": "resume"})
        mock_act.assert_called_once_with(["set_property", "pause", False], "Resumed.")

    def test_soundcloud_volume_up_calls_mpv_add(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Volume up.") as mock_act:
            m._execute_music_control({"action": "volume_up"})
        mock_act.assert_called_once_with(["add", "volume", 5], "Volume up.")

    def test_soundcloud_volume_down_calls_mpv_add_negative(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Volume down.") as mock_act:
            m._execute_music_control({"action": "volume_down"})
        mock_act.assert_called_once_with(["add", "volume", -5], "Volume down.")

    def test_spotify_active_dispatches_to_spotify_branch(self):
        """Spotify branch is exercised in test_spotify_skill once spotipy is wired.
        Here we just verify the handler routes to the right method."""
        m = _make_manager()
        m._active_music_source = "spotify"
        with patch.object(m, "_music_control_spotify", return_value="Skipped.") as mock_sp:
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Skipped.")
        mock_sp.assert_called_once_with("skip")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlDispatch -v`
Expected: 9 FAILs (`AttributeError: '_execute_music_control'` / `_music_control_spotify`).

- [ ] **Step 3: Add the dispatch handler + stub for spotify branch**

Append to `core/container_manager.py` (after `_stop_spotify_playback`):

```python
    _MUSIC_CONTROL_ACTIONS = (
        "stop", "pause", "resume", "skip", "volume_up", "volume_down",
    )

    def _execute_music_control(self, tool_input: dict) -> str:
        """Transport router — dispatches to the active music source's backend."""
        action = str(tool_input.get("action") or "").strip().lower()
        if action not in self._MUSIC_CONTROL_ACTIONS:
            return f"Unknown music-control action: {action!r}"

        source = self._active_music_source
        if source is None:
            return "Nothing is playing."

        if source == "soundcloud":
            return self._music_control_soundcloud(action)
        if source == "spotify":
            return self._music_control_spotify(action)
        return f"Unknown active music source: {source!r}"

    def _music_control_soundcloud(self, action: str) -> str:
        """Reuse the existing soundcloud transport implementations."""
        if action == "stop":
            return self._stop_mpv()
        if action == "pause":
            return self._mpv_action_or_idle(["set_property", "pause", True], "Paused.")
        if action == "resume":
            return self._mpv_action_or_idle(["set_property", "pause", False], "Resumed.")
        if action == "skip":
            return self._mpv_action_or_idle(["playlist-next"], "Skipped.")
        if action == "volume_up":
            return self._mpv_action_or_idle(["add", "volume", 5], "Volume up.")
        if action == "volume_down":
            return self._mpv_action_or_idle(["add", "volume", -5], "Volume down.")
        return f"Unhandled action: {action}"

    def _music_control_spotify(self, action: str) -> str:
        """Stub — filled in by Task 11 once the Spotify backend exists."""
        return "Spotify isn't set up yet"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: 14 passed (5 from Task 5 + 9 new).

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_music_control.py
git commit -m "feat(music-control): transport router dispatching to active source"
```

---

## Task 7: Register `music-control` native handler + create skill files (TDD)

**Files:**
- Modify: `core/container_manager.py` (register handler in `_native_handlers`)
- Create: `skills/music-control/SKILL.md`
- Create: `skills/music-control/config.yaml`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_music_control.py`:

```python
class MusicControlSkillRegistration(unittest.TestCase):
    def test_native_handler_registered(self):
        m = _make_manager()
        self.assertIn("music-control", m._native_handlers)
        self.assertIs(m._native_handlers["music-control"], m._execute_music_control)

    def test_skill_loads_via_skill_loader(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        skills = loader.load_all()
        self.assertIn("music-control", skills)
        s = skills["music-control"]
        self.assertEqual(s.execution_config.get("type"), "native")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlSkillRegistration -v`
Expected: 2 FAILs.

- [ ] **Step 3: Register the handler**

In `core/container_manager.py:__init__`, find the `self._native_handlers = {…}` block (around line 61) and add:

```python
        self._native_handlers = {
            "install-skill": self._execute_install_skill,
            "set-env-var": self._execute_set_env_var,
            "save-memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall-session": self._execute_recall_session,
            "update-skill-hints": self._execute_update_skill_hints,
            "music-control": self._execute_music_control,   # NEW
        }
```

- [ ] **Step 4: Create `skills/music-control/SKILL.md`**

```markdown
---
name: music-control
description: Transport controls (stop, pause, resume, skip, volume) for whatever music is currently playing — Spotify or SoundCloud. Routes to the active source automatically.
---

# Music Control Skill

## When to use

Use for transport commands while music is already playing:

- "stop", "stop music", "halt"
- "pause", "pause the music"
- "resume", "continue", "unpause"
- "skip", "next track"
- "volume up", "louder", "turn it up"
- "volume down", "quieter", "turn it down"

This skill does NOT start music. Use `spotify` or `soundcloud` for that.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [stop, pause, resume, skip, volume_up, volume_down]
    description: Transport command to issue against whichever source is playing.
required:
  - action
```

## How to respond

Brief acknowledgement: "Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up.", "Volume down.". If nothing is playing, say so plainly ("Nothing is playing.").
```

- [ ] **Step 5: Create `skills/music-control/config.yaml`**

```yaml
type: native
timeout_seconds: 10
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: 16 passed.

- [ ] **Step 7: Commit**

```bash
git add core/container_manager.py skills/music-control/ tests/test_music_control.py
git commit -m "feat(music-control): register native handler + skill manifest"
```

---

## Task 8: Repoint `intent_patterns.yaml` transport patterns to `music-control` (TDD)

**Files:**
- Modify: `config/intent_patterns.yaml`
- Modify: `tests/test_tier_router.py`

- [ ] **Step 1: Update the existing tier router tests for the new dispatch target**

In `tests/test_tier_router.py`, find the `TestMusicTransportPatterns` class. Its assertions currently expect `r.skill == "soundcloud"` for transport. Update them to expect `music-control` instead:

```python
class TestMusicTransportPatterns(unittest.TestCase):
    def setUp(self):
        self.router = _make_router()

    def test_stop_routes_to_stop(self):
        r = self.router.route("stop the music")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "music-control")
        self.assertEqual(r.args, {"action": "stop"})

    def test_halt_routes_to_stop(self):
        r = self.router.route("halt")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "music-control")
        self.assertEqual(r.args, {"action": "stop"})

    def test_pause_routes_to_pause(self):
        r = self.router.route("pause the music")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "music-control")
        self.assertEqual(r.args, {"action": "pause"})

    def test_pause_does_not_match_stop_pattern(self):
        r = self.router.route("pause")
        self.assertEqual(r.args.get("action"), "pause")

    def test_resume_routes_to_resume(self):
        r = self.router.route("resume")
        self.assertEqual(r.skill, "music-control")
        self.assertEqual(r.args, {"action": "resume"})

    def test_continue_routes_to_resume(self):
        r = self.router.route("continue music")
        self.assertEqual(r.args, {"action": "resume"})
```

(Update any other transport tests in the same file the same way: change `skill="soundcloud"` to `skill="music-control"`. The remaining tests for `play`/`stop` patterns that aren't transport stay unchanged.)

Also update `TestDispatchPatterns` similarly — its tests for `stop`/`pause`/`volume up`/`volume down` should expect `music-control`:

```python
    def test_stop_routes_direct(self):
        router = _make_router()
        result = router.route("stop")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "music-control")
        self.assertEqual(result.args, {"action": "stop"})

    def test_stop_music_routes_direct(self):
        router = _make_router()
        result = router.route("stop music")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "music-control")

    def test_pause_routes_direct(self):
        router = _make_router()
        result = router.route("pause")
        self.assertEqual(result.tier, "direct")

    def test_volume_up_routes_direct(self):
        router = _make_router()
        result = router.route("volume up")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.args, {"action": "volume_up"})

    def test_volume_down_routes_direct(self):
        router = _make_router()
        result = router.route("volume down")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.args, {"action": "volume_down"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tier_router.py -v`
Expected: ~10 FAILs in the dispatch and transport tests (`AssertionError: 'soundcloud' != 'music-control'`).

- [ ] **Step 3: Update `config/intent_patterns.yaml`**

Change all six transport patterns' `skill:` from `soundcloud` to `music-control`:

```yaml
dispatch:
  - pattern: "^(stop|halt)(\\s+(the\\s+)?(music|playing|audio))?[.!?]?$"
    skill: music-control
    args: {action: stop}

  - pattern: "^pause(\\s+(the\\s+)?(music|playing|audio))?[.!?]?$"
    skill: music-control
    args: {action: pause}

  - pattern: "^(resume|continue|unpause)(\\s+(the\\s+)?(music|playing|audio))?[.!?]?$"
    skill: music-control
    args: {action: resume}

  - pattern: "^(skip|next)(\\s+(this\\s+)?(song|track))?[.!?]?$"
    skill: music-control
    args: {action: skip}

  - pattern: "^(volume up|turn it up|louder)[.!?]?$"
    skill: music-control
    args: {action: volume_up}

  - pattern: "^(volume down|turn it down|quieter|lower the volume)[.!?]?$"
    skill: music-control
    args: {action: volume_down}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tier_router.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add config/intent_patterns.yaml tests/test_tier_router.py
git commit -m "feat(router): repoint transport patterns to music-control"
```

---

## Task 9: `_execute_spotify` — `play` action (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Create: `tests/test_spotify_skill.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spotify_skill.py`:

```python
"""Tests for the spotify native skill handler."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_manager():
    from core.container_manager import ContainerManager
    return ContainerManager()


def _fake_track(name="Cool Song", artist="Some Band", uri="spotify:track:abc"):
    return {
        "name": name,
        "uri": uri,
        "artists": [{"name": artist}],
    }


class SpotifyPlayAction(unittest.TestCase):
    def test_no_query_returns_friendly_error(self):
        m = _make_manager()
        result = m._execute_spotify({"action": "play", "query": ""})
        self.assertIn("no", result.lower())
        self.assertNotIn("Traceback", result)

    def test_auth_missing_surfaces_setup_message(self):
        m = _make_manager()
        from core.spotify_auth import SpotifyAuthMissing

        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("SPOTIFY_CLIENT_ID...")):
            result = m._execute_spotify({"action": "play", "query": "country"})
        self.assertIn("Spotify isn't set up", result)

    def test_play_with_results_starts_playback(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": [_fake_track()]}}
        sp.devices.return_value = {"devices": [
            {"id": "dev1", "name": "Pi librespot", "is_active": True}
        ]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_stop_all_music") as mock_stop:
            result = m._execute_spotify({"action": "play", "query": "cool song"})

        sp.search.assert_called_once()
        mock_stop.assert_called_once()
        sp.start_playback.assert_called_once_with(
            device_id="dev1", uris=["spotify:track:abc"]
        )
        self.assertEqual(m._active_music_source, "spotify")
        self.assertIn("Cool Song", result)
        self.assertIn("Some Band", result)

    def test_play_with_no_results_returns_friendly_message(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": []}}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play", "query": "obscure"})

        self.assertIn("Couldn't find", result)
        sp.start_playback.assert_not_called()
        self.assertIsNone(m._active_music_source)

    def test_play_with_no_active_device_returns_setup_hint(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": [_fake_track()]}}
        sp.devices.return_value = {"devices": []}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play", "query": "x"})

        self.assertIn("librespot", result.lower())
        sp.start_playback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py -v`
Expected: 5 FAILs (`AttributeError: _execute_spotify`).

- [ ] **Step 3: Add the spotify handler**

Append to `core/container_manager.py`:

```python
    def _execute_spotify(self, tool_input: dict) -> str:
        """Native handler for the spotify skill. Two actions: play and play_playlist."""
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing

        action = str(tool_input.get("action") or "play").strip().lower()

        if action == "play":
            query = str(tool_input.get("query", "")).strip()
            if not query:
                return "No search query provided for Spotify."
            try:
                sp = get_spotify_client()
            except SpotifyAuthMissing as exc:
                return f"Spotify isn't set up: {exc}"
            return self._spotify_play_track(sp, query)

        if action == "play_playlist":
            name = str(tool_input.get("name", "")).strip()
            if not name:
                return "No playlist name provided."
            try:
                sp = get_spotify_client()
            except SpotifyAuthMissing as exc:
                return f"Spotify isn't set up: {exc}"
            return self._spotify_play_playlist(sp, name)

        return f"Unknown spotify action: {action!r}"

    def _spotify_play_track(self, sp, query: str) -> str:
        try:
            results = sp.search(q=query, type="track", limit=10)
        except Exception as exc:
            return f"Couldn't reach Spotify right now: {exc}"
        items = (results.get("tracks") or {}).get("items") or []
        if not items:
            return f"Couldn't find anything matching {query!r} on Spotify."

        track = items[0]
        track_uri = track["uri"]
        title = track.get("name", "track")
        artists = track.get("artists") or []
        artist = artists[0]["name"] if artists else "unknown"

        device_id = self._spotify_device_id(sp)
        if device_id is None:
            return ("Spotify is set up but no Connect device is available. "
                    "Check that librespot is running on the Pi.")

        self._stop_all_music()
        try:
            sp.start_playback(device_id=device_id, uris=[track_uri])
        except Exception as exc:
            return f"Couldn't start Spotify playback: {exc}"

        self._active_music_source = "spotify"
        return f"Now playing: {title} by {artist}"

    def _spotify_play_playlist(self, sp, name: str) -> str:
        # Filled in by Task 10
        return "play_playlist not implemented yet"

    def _spotify_device_id(self, sp) -> str | None:
        """Return the first available Spotify Connect device id, preferring active."""
        try:
            devices = (sp.devices() or {}).get("devices") or []
        except Exception:
            return None
        if not devices:
            return None
        # Prefer an already-active device, fall back to first available.
        active = [d for d in devices if d.get("is_active")]
        if active:
            return active[0].get("id")
        return devices[0].get("id")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_spotify_skill.py
git commit -m "feat(spotify): play action with search + Connect device discovery"
```

---

## Task 10: `_spotify_play_playlist` + fuzzy match helper (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Modify: `tests/test_spotify_skill.py`

- [ ] **Step 1: Append the failing tests**

Add to `tests/test_spotify_skill.py`:

```python
class SpotifyPlayPlaylist(unittest.TestCase):
    def test_no_name_returns_friendly_error(self):
        m = _make_manager()
        result = m._execute_spotify({"action": "play_playlist", "name": ""})
        self.assertIn("no playlist name", result.lower())

    def test_play_playlist_exact_match(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {"name": "COUNTRY", "uri": "spotify:playlist:c1"},
                {"name": "HIPHOP", "uri": "spotify:playlist:h1"},
            ],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_stop_all_music") as mock_stop:
            result = m._execute_spotify({"action": "play_playlist", "name": "COUNTRY"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:c1"
        )
        mock_stop.assert_called_once()
        self.assertEqual(m._active_music_source, "spotify")
        self.assertIn("COUNTRY", result)

    def test_play_playlist_case_insensitive(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"name": "COUNTRY", "uri": "spotify:playlist:c1"}],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "country"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:c1"
        )

    def test_play_playlist_substring_match(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {"name": "Workout 2026", "uri": "spotify:playlist:w1"},
                {"name": "Dinner Vibes", "uri": "spotify:playlist:d1"},
            ],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "workout"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:w1"
        )

    def test_play_playlist_no_match_returns_message(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"name": "JAZZ", "uri": "spotify:playlist:j1"}],
            "next": None,
        }

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "metal"})

        self.assertIn("Couldn't find", result)
        sp.start_playback.assert_not_called()
        self.assertIsNone(m._active_music_source)


class FuzzyMatchPlaylistHelper(unittest.TestCase):
    def test_exact_case_insensitive(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertEqual(_fuzzy_match_playlist("country", ["COUNTRY", "JAZZ"]), "COUNTRY")

    def test_substring_either_direction(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertEqual(
            _fuzzy_match_playlist("workout", ["Workout 2026", "Dinner"]),
            "Workout 2026",
        )

    def test_levenshtein_fallback(self):
        from core.container_manager import _fuzzy_match_playlist
        # "countrie" -> "COUNTRY" via difflib close-match
        self.assertEqual(_fuzzy_match_playlist("countrie", ["COUNTRY", "POP"]), "COUNTRY")

    def test_no_match_returns_none(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertIsNone(_fuzzy_match_playlist("metal", ["COUNTRY", "POP"]))

    def test_empty_names_returns_none(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertIsNone(_fuzzy_match_playlist("anything", []))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py -v`
Expected: 10 FAILs (5 new in `SpotifyPlayPlaylist` plus 5 new in `FuzzyMatchPlaylistHelper`).

- [ ] **Step 3: Add the fuzzy match helper at module level**

In `core/container_manager.py`, near the top of the file (after the existing module-level constants/imports), add:

```python
def _fuzzy_match_playlist(query: str, names: list[str]) -> str | None:
    """Return the best-matching playlist name from `names`, or None.

    Match precedence (per spec):
      1. Exact (case-insensitive)
      2. Substring (either direction, case-insensitive)
      3. difflib close-match with cutoff 0.7 (~30% Levenshtein distance)
    """
    if not names:
        return None

    q_lower = query.lower()
    name_lower = [n.lower() for n in names]

    # 1. Exact case-insensitive
    for original, lower in zip(names, name_lower):
        if lower == q_lower:
            return original

    # 2. Substring either direction
    for original, lower in zip(names, name_lower):
        if q_lower in lower or lower in q_lower:
            return original

    # 3. difflib close-match (Levenshtein-ish, ratio ≥ 0.7)
    import difflib
    best = difflib.get_close_matches(q_lower, name_lower, n=1, cutoff=0.7)
    if best:
        idx = name_lower.index(best[0])
        return names[idx]

    return None
```

- [ ] **Step 4: Replace the `_spotify_play_playlist` stub with the real implementation**

Find the stub from Task 9 and replace its body:

```python
    def _spotify_play_playlist(self, sp, name: str) -> str:
        # Paginate through all of the user's saved playlists. Spotify caps at
        # 50 per page; users with hundreds of playlists need the loop.
        playlists: list[dict] = []
        try:
            res = sp.current_user_playlists(limit=50)
        except Exception as exc:
            return f"Couldn't reach Spotify right now: {exc}"
        while res:
            playlists.extend(res.get("items") or [])
            if res.get("next"):
                try:
                    res = sp.next(res)
                except Exception:
                    break
            else:
                break

        if not playlists:
            return "You don't have any saved playlists on Spotify."

        names = [p["name"] for p in playlists if p.get("name")]
        matched_name = _fuzzy_match_playlist(name, names)
        if matched_name is None:
            return f"Couldn't find a playlist named {name!r} in your library."

        playlist = next(p for p in playlists if p.get("name") == matched_name)

        device_id = self._spotify_device_id(sp)
        if device_id is None:
            return ("Spotify is set up but no Connect device is available. "
                    "Check that librespot is running on the Pi.")

        self._stop_all_music()
        try:
            sp.start_playback(device_id=device_id, context_uri=playlist["uri"])
        except Exception as exc:
            return f"Couldn't start playlist: {exc}"

        self._active_music_source = "spotify"
        return f"Playing your {matched_name} playlist"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py -v`
Expected: 15 passed.

- [ ] **Step 6: Commit**

```bash
git add core/container_manager.py tests/test_spotify_skill.py
git commit -m "feat(spotify): play_playlist with fuzzy match (exact/substring/Levenshtein)"
```

---

## Task 11: Fill in `_stop_spotify_playback` (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Modify: `tests/test_music_control.py`

- [ ] **Step 1: Append the failing tests**

Add to `tests/test_music_control.py`:

```python
class StopSpotifyPlayback(unittest.TestCase):
    def test_no_op_when_active_source_is_not_spotify(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        # Should not even try to call get_spotify_client
        with patch("core.spotify_auth.get_spotify_client") as mock_get:
            m._stop_spotify_playback()
        mock_get.assert_not_called()

    def test_pauses_active_device_when_spotify_is_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            m._stop_spotify_playback()
        sp.pause_playback.assert_called_once_with(device_id="dev1")

    def test_swallows_auth_missing_silently(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        from core.spotify_auth import SpotifyAuthMissing
        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("missing")):
            m._stop_spotify_playback()  # must not raise

    def test_swallows_pause_errors_silently(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1"}]}
        sp.pause_playback.side_effect = RuntimeError("network")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            m._stop_spotify_playback()  # must not raise


class MusicControlSpotifyBranch(unittest.TestCase):
    def test_skip_calls_spotify_next_track(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        sp.next_track.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Skipped.")

    def test_pause_calls_spotify_pause_playback(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "pause"})
        sp.pause_playback.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Paused.")

    def test_resume_calls_spotify_start_playback_no_uris(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "resume"})
        sp.start_playback.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Resumed.")

    def test_volume_up_clamps_to_100(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True, "volume_percent": 98}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "volume_up"})
        sp.volume.assert_called_once_with(volume_percent=100, device_id="dev1")
        self.assertEqual(result, "Volume up.")

    def test_volume_down_clamps_to_0(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True, "volume_percent": 2}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "volume_down"})
        sp.volume.assert_called_once_with(volume_percent=0, device_id="dev1")
        self.assertEqual(result, "Volume down.")

    def test_stop_uses_pause_then_clears_active_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "stop"})
        sp.pause_playback.assert_called_once_with(device_id="dev1")
        self.assertIsNone(m._active_music_source)
        self.assertEqual(result, "Stopped.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: 10 FAILs in the new classes.

- [ ] **Step 3: Replace `_stop_spotify_playback` and `_music_control_spotify`**

In `core/container_manager.py`, replace both stubs:

```python
    def _stop_spotify_playback(self) -> None:
        """Pause Spotify if it's the active source. Best-effort, exception-safe."""
        if self._active_music_source != "spotify":
            return
        try:
            from core.spotify_auth import get_spotify_client, SpotifyAuthMissing
            try:
                sp = get_spotify_client()
            except SpotifyAuthMissing:
                return  # nothing usable to stop
            device_id = self._spotify_device_id(sp)
            if device_id:
                try:
                    sp.pause_playback(device_id=device_id)
                except Exception:
                    logger.exception("Spotify pause_playback failed")
        except Exception:
            logger.exception("_stop_spotify_playback failed")

    def _music_control_spotify(self, action: str) -> str:
        """Spotify-side of music-control transport dispatch."""
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing
        try:
            sp = get_spotify_client()
        except SpotifyAuthMissing as exc:
            return f"Spotify isn't set up: {exc}"

        device_id = self._spotify_device_id(sp)
        if device_id is None:
            return "Spotify Connect device unavailable."

        try:
            if action == "stop":
                sp.pause_playback(device_id=device_id)
                self._active_music_source = None
                return "Stopped."
            if action == "pause":
                sp.pause_playback(device_id=device_id)
                return "Paused."
            if action == "resume":
                sp.start_playback(device_id=device_id)
                return "Resumed."
            if action == "skip":
                sp.next_track(device_id=device_id)
                return "Skipped."
            if action in ("volume_up", "volume_down"):
                # Read current volume from device list; clamp 0-100.
                devices = (sp.devices() or {}).get("devices") or []
                this_dev = next(
                    (d for d in devices if d.get("id") == device_id), None
                )
                current = int((this_dev or {}).get("volume_percent") or 50)
                step = 5 if action == "volume_up" else -5
                new_vol = max(0, min(100, current + step))
                sp.volume(volume_percent=new_vol, device_id=device_id)
                return "Volume up." if action == "volume_up" else "Volume down."
        except Exception as exc:
            return f"Couldn't reach Spotify: {exc}"
        return f"Unhandled action: {action}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_music_control.py
git commit -m "feat(spotify): _stop_spotify_playback + music-control spotify branch"
```

---

## Task 12: Register `spotify` native handler + create skill files (TDD)

**Files:**
- Modify: `core/container_manager.py` (register handler in `_native_handlers`)
- Create: `skills/spotify/SKILL.md`
- Create: `skills/spotify/config.yaml`
- Modify: `tests/test_spotify_skill.py`

- [ ] **Step 1: Append the registration test**

Add to `tests/test_spotify_skill.py`:

```python
class SpotifySkillRegistration(unittest.TestCase):
    def test_native_handler_registered(self):
        m = _make_manager()
        self.assertIn("spotify", m._native_handlers)
        self.assertIs(m._native_handlers["spotify"], m._execute_spotify)

    @patch.dict("os.environ", {
        "SPOTIFY_CLIENT_ID": "x", "SPOTIFY_CLIENT_SECRET": "y",
    }, clear=False)
    def test_skill_loads_when_env_present(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        skills = loader.load_all()
        self.assertIn("spotify", skills)
        s = skills["spotify"]
        self.assertEqual(s.execution_config.get("type"), "native")

    @patch.dict("os.environ", {}, clear=True)
    def test_skill_skipped_when_env_missing(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        loader.load_all()
        self.assertIn("spotify", loader.skipped_skills)
        info = loader.skipped_skills["spotify"]
        self.assertIn("SPOTIFY_CLIENT_ID", " ".join(info.get("missing_env_vars", [])))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py::SpotifySkillRegistration -v`
Expected: 3 FAILs.

- [ ] **Step 3: Register the handler**

In `core/container_manager.py:__init__`, add to `_native_handlers`:

```python
        self._native_handlers = {
            "install-skill": self._execute_install_skill,
            "set-env-var": self._execute_set_env_var,
            "save-memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall-session": self._execute_recall_session,
            "update-skill-hints": self._execute_update_skill_hints,
            "music-control": self._execute_music_control,
            "spotify": self._execute_spotify,                  # NEW
        }
```

- [ ] **Step 4: Create `skills/spotify/SKILL.md`**

```markdown
---
name: spotify
description: Play music or saved playlists from Spotify. Default music backend — use this for "play X" / "play [artist]" / "play my [playlist]" requests unless explicitly asked for SoundCloud or for remixes/bootlegs/mashups.
metadata:
  miniclaw:
    requires:
      env:
        - SPOTIFY_CLIENT_ID
        - SPOTIFY_CLIENT_SECRET
---

# Spotify Skill

## When to use

This is the DEFAULT music source. Prefer it for:

- **Play a track or artist** — "play [song]", "play [artist]", "put on some [genre]"
- **Play a saved playlist** — "play my [name] playlist", "play my COUNTRY", "start my morning playlist"

For DJ remixes, bootlegs, mashups, or specific SoundCloud tracks, use the `soundcloud` skill instead. Trigger words that indicate SoundCloud: "remix", "bootleg", "mashup", "DJ set", "live set", or "on SoundCloud".

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, play_playlist]
    description: play searches the catalog; play_playlist plays a saved user playlist by name.
  query:
    type: string
    description: For play action — song / artist / genre query.
  name:
    type: string
    description: For play_playlist action — playlist name (fuzzy matched against the user's saved playlists).
required:
  - action
```

## How to respond

For `play`, confirm what's playing ("Now playing X by Y"). For `play_playlist`, confirm the playlist name. If setup is incomplete or the Connect device is unavailable, relay the error verbatim — it tells the user what to fix.

## Setup (one-time)

Before this skill works:

1. Register a Spotify Developer app at developer.spotify.com.
2. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`.
3. Add `http://localhost:8888/callback` to the dev app's Redirect URIs.
4. Run `python scripts/spotify_login.py` once and follow the browser flow.
5. On the Pi: `apt install librespot`, then open phone Spotify → tap Connect icon → tap Pi as a device once to pair librespot.
```

- [ ] **Step 5: Create `skills/spotify/config.yaml`**

```yaml
type: native
timeout_seconds: 30
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotify_skill.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add core/container_manager.py skills/spotify/ tests/test_spotify_skill.py
git commit -m "feat(spotify): register native handler + skill manifest with env requires"
```

---

## Task 13: Edit `_execute_soundcloud` + soundcloud SKILL.md (TDD)

**Files:**
- Modify: `core/container_manager.py`
- Modify: `skills/soundcloud/SKILL.md` (route LLM to remix/bootleg cases only)
- Modify: `tests/test_soundcloud_handler.py`

- [ ] **Step 1: Append the failing test**

Add to `tests/test_soundcloud_handler.py` (at the end of the appropriate test class — `TestPlayQueue`):

```python
    def test_play_calls_stop_all_music_first(self):
        """play must dispatch to the shared mutual-exclusion helper, not just
        terminate mpv — otherwise starting SoundCloud while Spotify is active
        leaves Spotify still playing."""
        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = self._yt_dlp_output(1)
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen"), \
             patch("pathlib.Path.write_text"), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.open", MagicMock()), \
             patch.object(self.manager, "_stop_all_music") as mock_stop_all:
            self.manager._execute_soundcloud({"action": "play", "query": "x"})

        mock_stop_all.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_soundcloud_handler.py::TestPlayQueue::test_play_calls_stop_all_music_first -v`
Expected: FAIL (current code calls `_terminate_mpv_process`, not `_stop_all_music`).

- [ ] **Step 3: Update `_execute_soundcloud`**

In `core/container_manager.py:_execute_soundcloud`, find the line:

```python
        # Stop any currently playing track before queueing a new search.
        self._terminate_mpv_process()
        if os.path.exists(self._mpv_socket_path):
            try:
                os.unlink(self._mpv_socket_path)
            except OSError:
                pass
```

Replace it with:

```python
        # Stop any currently playing music (Spotify or SoundCloud) before
        # queueing a new search. Using _stop_all_music() instead of just
        # _terminate_mpv_process keeps the mutual-exclusion invariant —
        # starting SoundCloud while Spotify is active must stop Spotify too.
        self._stop_all_music()
        if os.path.exists(self._mpv_socket_path):
            try:
                os.unlink(self._mpv_socket_path)
            except OSError:
                pass
```

Then, after the `mpv` subprocess.Popen succeeds and the now-playing JSON is written, set the active source. Find the block that ends with `return f"Now playing: {first_title}"` and insert the assignment:

```python
        now_playing_path = Path.home() / ".miniclaw" / "now_playing.json"
        try:
            import time as _time
            now_playing_path.parent.mkdir(parents=True, exist_ok=True)
            now_playing_path.write_text(
                json.dumps({"title": first_title, "timestamp": _time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        self._active_music_source = "soundcloud"   # NEW
        return f"Now playing: {first_title}"
```

- [ ] **Step 4: Update `skills/soundcloud/SKILL.md` so the LLM only prefers SoundCloud for remix-style requests**

Replace the existing `When to use` section with:

```markdown
## When to use

This skill is the secondary music backend, used only when SoundCloud is specifically what the user wants. Prefer the `spotify` skill for everyday "play me X" requests; SoundCloud doesn't have most major-label catalog and is best for niche / DJ content.

Pick this skill when the user's phrasing includes any of:
- "remix", "bootleg", "mashup"
- "DJ set", "live set"
- "on SoundCloud" / "from SoundCloud"
- An obscure artist Spotify doesn't have

Transport (stop/pause/skip/volume) is handled by `music-control`, not this skill — don't call this skill's transport actions directly. The transport actions are kept in this file's input schema for backward compat only; new code routes via `music-control`.

Triggers covered by this skill:
- **Play music** — "play remix of [song]", "play [DJ name] live set", "play that [thing] on SoundCloud"
```

(Leave the rest of the SKILL.md — frontmatter, Inputs, How to respond — unchanged.)

- [ ] **Step 5: Run all soundcloud tests to verify**

Run: `.venv/bin/python -m pytest tests/test_soundcloud_handler.py -v`
Expected: all pass (existing + 1 new). Note: any older test that relied on `_terminate_mpv_process` being called directly may need updating — but the existing tests mock `subprocess.Popen` and `_stop_mpv` indirectly, so they should keep passing.

- [ ] **Step 6: Commit**

```bash
git add core/container_manager.py skills/soundcloud/SKILL.md tests/test_soundcloud_handler.py
git commit -m "fix(soundcloud): _stop_all_music + active-source set + SKILL.md scopes to remix-style requests"
```

---

## Task 14: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the Spotify section**

Add to the end of `.env.example`:

```
# Spotify (optional — required only for the spotify skill)
# Setup:
#   1. Register a Spotify Developer app at developer.spotify.com (free).
#   2. Add http://localhost:8888/callback to the app's Redirect URIs.
#   3. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET below from the dashboard.
#   4. Run: python scripts/spotify_login.py  (one-time browser auth).
#   5. On Pi: apt install librespot; pair it once via phone Spotify Connect.
# When env vars are absent, the spotify skill is automatically marked as
# unavailable and Claude tells you what's missing instead of failing.
# SPOTIFY_CLIENT_ID=your_client_id
# SPOTIFY_CLIENT_SECRET=your_client_secret
# SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(env): document Spotify setup in .env.example"
```

---

## Task 15: Run the full test suite

**Files:**
- (none)

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: roughly 454 passed (434 baseline + ~20 new). 0 failed.

- [ ] **Step 2: If any unexpected failures, fix inline**

Most likely sources of incidental failure:
- Existing tier-router tests that hard-code `skill: soundcloud` for transport — already updated in Task 8, but double-check none were missed.
- `tests/test_orchestrator_routing.py` direct-route tests — they test the dispatch shape; if any explicitly construct a `RouteResult(skill="soundcloud", action=...)` for transport, update to `music-control`.

- [ ] **Step 3: Commit any followup fixes**

```bash
git add tests/
git commit -m "test: align with music-control transport routing"
```

(Skip this commit if step 2 produced no changes.)

---

## Task 16: Pi smoke test (manual checklist)

**Files:**
- (none — runtime verification)

This is not automated. Mason runs through this on his Pi after pulling main.

- [ ] **Step 1: Pull and install**

```bash
ssh pi 'cd ~/miniclaw && git pull --ff-only origin main && .venv/bin/python -m pip install -r requirements.txt'
```

- [ ] **Step 2: Set Spotify env vars**

Add to `~/miniclaw/.env`:
```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
```
(Use the Client ID Mason already has, plus the Client Secret from the same dev-app dashboard. Add `http://localhost:8888/callback` to the app's Redirect URIs first.)

- [ ] **Step 3: Run the OAuth login**

```bash
ssh pi 'cd ~/miniclaw && .venv/bin/python scripts/spotify_login.py'
```
Follow the browser dance. Confirm `~/.miniclaw/spotify-tokens.json` exists.

- [ ] **Step 4: Install and pair librespot**

```bash
ssh pi 'sudo apt install -y librespot'
ssh pi 'librespot --name "MiniClaw Pi" --backend alsa --device default &'
```
Open phone Spotify → tap Connect icon → tap "MiniClaw Pi" once.

- [ ] **Step 5: Run the assistant and verify each scenario**

```bash
ssh pi 'cd ~/miniclaw && ./run.sh --voice'
```

Verify:
- [ ] **A.** Startup banner reads `TTS backend: kokoro-onnx (af_heart, fp32 …)` and skill listing includes both `spotify` and `music-control`.
- [ ] **B.** "Hey jarvis. Play me country music." → music starts within 2-3s.
- [ ] **C.** "Skip." → next track.
- [ ] **D.** "Pause." → silence. "Resume." → music continues.
- [ ] **E.** "Stop." → silence. "Hey jarvis. Play my COUNTRY playlist." → playlist starts.
- [ ] **F.** "Hey jarvis. Play me a remix of Despacito." → routes to SoundCloud (mpv comes back).
- [ ] **G.** Then "Skip." while SoundCloud is playing → SoundCloud advances, not Spotify.
- [ ] **H.** Disconnect Pi from network → "Hey jarvis. Play me country." → friendly error message (no crash).

- [ ] **Step 6: If smoke passes, no further commits. If not, file fixes in followup tasks.**

---

## Self-Review Notes

**Spec coverage check:**

- spotify skill (play / play_playlist) — Tasks 9, 10
- music-control skill (transport router) — Tasks 6, 7, 11
- soundcloud skill light edits (`_stop_all_music` + `When to use` rewrite) — Task 13
- ContainerManager `_active_music_source` + `_stop_all_music` + `_stop_spotify_playback` — Tasks 5, 11
- `core/spotify_auth.py` — Tasks 2, 3
- `scripts/spotify_login.py` — Task 4
- `intent_patterns.yaml` repointing — Task 8
- `requires.env` self-gating for spotify — Task 12 (test_skill_skipped_when_env_missing)
- `.env.example` documentation — Task 14
- librespot setup — Task 12 SKILL.md + Task 16 smoke test (Pi-side, not in repo)
- Pi smoke test — Task 16

**Deliberate spec deviations** (with reasons):

- Spec mentions caching the Spotify Connect device id with a stale-id retry path. Plan calls `sp.devices()` fresh on every play — costs ~50ms per request, removes a layer of state and a retry path that's never been needed in practice. If device discovery becomes a bottleneck under heavy use, add caching as a follow-up.
- Spec mentions an explicit 401 retry path. Plan relies on `spotipy.SpotifyOAuth` + `CacheFileHandler` to refresh transparently before the request fires — eliminates the retry path entirely. If real-world tokens get into a pathological half-expired state, add an explicit retry as a follow-up.
- Spec says "atomic token cache write (tmp file + rename)". Plan delegates to `spotipy.CacheFileHandler`, which spotipy maintains. We don't reimplement the file write.

**Type / signature consistency check:**

- `_active_music_source` is consistently `str | None` everywhere it's set or read.
- `_fuzzy_match_playlist(query: str, names: list[str]) -> str | None` — same signature in helper definition (Task 10 step 3) and tests (Task 10 step 1).
- `_spotify_device_id(sp) -> str | None` — same shape in `_spotify_play_track` and `_music_control_spotify`.
- Native handler keys: `"music-control"` and `"spotify"` (kebab-case, matching skill name in frontmatter — checked by skill_loader).
- `SCOPES`, `TOKEN_CACHE_PATH`, `DEFAULT_REDIRECT_URI` are imported by `scripts/spotify_login.py` from `core.spotify_auth`; same names in both files.

**Placeholder scan:** clean (grep for TBD/TODO/FIXME/XXX returned nothing).

**Scope check:** single focused project — three skills + one auth helper + one config edit. Fits one implementation plan; no decomposition needed. ~20 new tests, comfortably under the test-suite budget.
