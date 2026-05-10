# music-control external Spotify playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user starts Spotify Connect playback from their phone on the pinned MiniClaw device, voice transport commands ("turn it down", "pause") should route to Spotify instead of returning "Nothing is playing."

**Architecture:** Add a `_detect_external_spotify_playback()` helper to `ContainerManager` that probes Spotify's `current_playback()` API and returns `"spotify"` if the active playback's device.id matches the pinned device. Update the gate in `_execute_music_control` to fall back to this probe when `_active_music_source` is None. Re-probe per call (no persistence). Transport-only.

**Tech Stack:** Python, spotipy (existing), existing test pattern in `tests/test_music_control.py` (unittest + mocked `get_spotify_client`).

**Spec:** `docs/superpowers/specs/2026-05-10-music-control-external-playback-design.md`

---

## File Structure

| File | Purpose |
|------|---------|
| `core/container_manager.py` (modify) | Add `_detect_external_spotify_playback()`, update the gate in `_execute_music_control` |
| `tests/test_music_control.py` (modify) | Add a new `MusicControlExternalSpotifyPlayback` test class with 6 tests |

Both changes ship in one task because the helper is tiny (~15 lines) and the gate change is one line — splitting them across tasks adds bookkeeping without value.

---

### Task 1: Probe Spotify before falling back to "Nothing is playing"

**Files:**
- Modify: `core/container_manager.py:840-854` (`_execute_music_control`) and add helper above it
- Modify: `tests/test_music_control.py` (append new test class)

- [ ] **Step 1: Add the failing tests**

Append this test class to `tests/test_music_control.py` after the existing `MusicControlSpotifyBranch` class and before the `if __name__ == "__main__":` block:

```python
class MusicControlExternalSpotifyPlayback(unittest.TestCase):
    """Phone-initiated Spotify Connect playback should route via _execute_music_control."""

    def _sp_with_playback(self, device_id, is_playing=True):
        """Build a mock spotify client whose current_playback() reports the given device."""
        sp = MagicMock()
        sp.current_playback.return_value = {
            "is_playing": is_playing,
            "device": {"id": device_id},
        }
        sp.devices.return_value = {
            "devices": [{"id": device_id, "is_active": True, "volume_percent": 50}]
        }
        return sp

    def test_phone_initiated_playback_on_pinned_device_routes_to_spotify(self):
        m = _make_manager()
        m._active_music_source = None  # phone started it, MiniClaw didn't track it
        sp = self._sp_with_playback("dev1")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "volume_down"})
        sp.volume.assert_called_once_with(volume_percent=45, device_id="dev1")
        self.assertEqual(result, "Volume down.")

    def test_playback_on_different_device_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = self._sp_with_playback("phone-speaker-id")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "pause"})
        self.assertEqual(result, "Nothing is playing.")
        sp.pause_playback.assert_not_called()

    def test_no_playback_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = MagicMock()
        sp.current_playback.return_value = None
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")

    def test_paused_playback_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = self._sp_with_playback("dev1", is_playing=False)
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")

    def test_spotify_auth_missing_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        from core.spotify_auth import SpotifyAuthMissing
        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("missing")):
            result = m._execute_music_control({"action": "pause"})
        self.assertEqual(result, "Nothing is playing.")

    def test_spotify_api_exception_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = MagicMock()
        sp.current_playback.side_effect = RuntimeError("network blip")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_music_control.py::MusicControlExternalSpotifyPlayback -v`

Expected: 5 of 6 fail with `AssertionError: 'Nothing is playing.' != 'Volume down.'` (or equivalent), because the current gate returns `"Nothing is playing."` immediately. The test that *expects* "Nothing is playing." (different-device case) may incidentally pass — verify by reading the test names against pytest's output. The success-path test definitely fails.

- [ ] **Step 3: Add the helper method to `ContainerManager`**

Open `core/container_manager.py`. Find the `_execute_music_control` method (around line 840). Insert this helper *immediately above* it:

```python
    def _detect_external_spotify_playback(self) -> str | None:
        """Probe Spotify for active playback on the pinned MiniClaw device.

        Returns "spotify" if the user has phone-initiated Spotify Connect
        playback running on the device this MiniClaw owns. Returns None if
        Spotify isn't set up, no playback is active, playback is paused, or
        playback is on a different device.
        """
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing
        try:
            sp = get_spotify_client()
        except SpotifyAuthMissing:
            return None
        try:
            playback = sp.current_playback()
            if not playback or not playback.get("is_playing"):
                return None
            playback_device_id = (playback.get("device") or {}).get("id")
            pinned_device_id = self._spotify_device_id(sp)
            if playback_device_id and playback_device_id == pinned_device_id:
                return "spotify"
        except Exception:
            logger.exception("_detect_external_spotify_playback failed")
        return None
```

- [ ] **Step 4: Update the gate in `_execute_music_control`**

In the same file, find this block (currently lines 846-848):

```python
        source = self._active_music_source
        if source is None:
            return "Nothing is playing."
```

Replace it with:

```python
        source = self._active_music_source or self._detect_external_spotify_playback()
        if source is None:
            return "Nothing is playing."
```

That's the only production-code change. The rest of `_execute_music_control` is untouched.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlExternalSpotifyPlayback -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full music_control test file to check no regressions**

Run: `.venv/bin/python -m pytest tests/test_music_control.py -v`
Expected: all tests pass (existing + 6 new). The pre-existing `test_no_active_source_returns_nothing_playing` still passes because `get_spotify_client()` raises `SpotifyAuthMissing` in test envs without configured creds, so the new probe returns None and the gate behaves identically.

- [ ] **Step 7: Run the full repo test suite to check no broader regressions**

Run: `.venv/bin/python -m pytest -x`
Expected: all tests pass. (Suite runs ~4–5 minutes.)

- [ ] **Step 8: Commit**

```bash
cd /home/daedalus/linux/miniclaw
git add core/container_manager.py tests/test_music_control.py
git commit -m "feat(music-control): route phone-initiated Spotify playback through transport gate

When the user starts Spotify Connect playback from their phone on the
pinned MiniClaw device, _active_music_source stays None — but voice
transport commands should still work. Add _detect_external_spotify_playback()
that probes sp.current_playback() and returns \"spotify\" if active
playback's device.id matches the pinned MiniClaw device. Re-probe per
call so MiniClaw stays accurate when playback ends from the phone side."
```

- [ ] **Step 9: Push**

```bash
git push origin main
```

---

### Task 2: Real-Pi verification (manual)

**Files:** none — this is a hardware checklist.

This task does not produce code. Each step is a manual action with an explicit success criterion. Run on the Pi.

- [ ] **Step 1: Pull latest on the Pi**

```bash
ssh pi "cd ~/miniclaw && git pull"
```

- [ ] **Step 2: Restart MiniClaw to pick up the change**

```bash
ssh pi "systemctl --user restart miniclaw"
```

Wait ~30s for MiniClaw to finish booting (watch via `journalctl --user -u miniclaw -f` until you see the wake-loop ready line).

- [ ] **Step 3: Start Spotify playback from the phone**

On your phone:
1. Open Spotify, start any track playing.
2. Tap the device-picker icon, select "MiniClaw".
3. Confirm audio is now coming from the Pi's speakers, not the phone.

- [ ] **Step 4: Issue voice transport commands**

Say each, one at a time, and confirm the response:

| Command | Expected behavior |
|---------|-------------------|
| "Jarvis, turn the volume down" | Volume drops by 5%; MiniClaw says "Volume down." |
| "Jarvis, turn the volume up" | Volume rises by 5%; MiniClaw says "Volume up." |
| "Jarvis, pause" | Playback pauses; MiniClaw says "Paused." |
| "Jarvis, resume" | Playback resumes; MiniClaw says "Resumed." |
| "Jarvis, skip" | Track skips to next; MiniClaw says "Skipped." |

If any command instead returns "Nothing is playing", capture the journal output:

```bash
ssh pi "journalctl --user -u miniclaw --since '5 minutes ago' | grep -iE 'music-control|spotify|detect'"
```

- [ ] **Step 5: Verify the negative case still works**

Stop playback entirely (pause from your phone, not via MiniClaw). Wait 5s. Then say "Jarvis, turn it up".

Expected: MiniClaw says "Nothing is playing." (because the probe sees `is_playing: false` and returns None).

- [ ] **Step 6: Document the result**

If everything passed, the feature is shipped. If anything failed, capture the journal output and either open an issue or save a project memory describing the failure mode.

---

## Done criteria

- All 6 new tests pass: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlExternalSpotifyPlayback -v`
- Full music_control file still green: `.venv/bin/python -m pytest tests/test_music_control.py`
- Full repo suite green: `.venv/bin/python -m pytest -x`
- Pi verification: phone-initiated Spotify playback responds to voice transport commands (Task 2, Step 4)
- Negative case still honored: paused phone playback → "Nothing is playing" (Task 2, Step 5)
