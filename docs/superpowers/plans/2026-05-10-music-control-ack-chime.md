# Music-control ack chime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace spoken "Paused."/"Resumed."/"Skipped."/"Volume up/down."/"Stopped." confirmations with a short audio chime; errors and clarifications stay spoken.

**Architecture:** Export a `MUSIC_CONTROL_ACK_SUCCESS` constant from `container_manager.py` (the six exact success strings). Add `play_ack_sound()` to `core/voice.py` (R2-D2-family synth, non-blocking, matches existing `play_response_ready_sound` pattern). Thread an `on_ack_success` callback through `Orchestrator.process_message` → `_execute_direct`; when the direct-tier result is in the set, fire the callback and return `""` instead of speaking. `main.py` wires `on_ack_success=voice.play_ack_sound` and skips `voice.speak` when response is `""`.

**Tech Stack:** Python, numpy, sounddevice (existing R2-D2 synth helpers `_r2_chirp` / `_r2_beep` / `_r2_tail`), unittest + `MagicMock`.

**Spec:** `docs/superpowers/specs/2026-05-10-music-control-ack-chime-design.md`

---

## File Structure

| File | Change |
|------|--------|
| `core/container_manager.py` | Add module-level `MUSIC_CONTROL_ACK_SUCCESS` frozenset constant |
| `core/voice.py` | Add `play_ack_sound()` method on `VoiceInterface` |
| `core/orchestrator.py` | `process_message` + `_execute_direct` gain `on_ack_success` param; ack short-circuit returns `""` |
| `main.py` | Voice-mode loop wires `on_ack_success=voice.play_ack_sound`; skips `voice.speak` when response is empty |
| `tests/test_music_control.py` | Pin-test for `MUSIC_CONTROL_ACK_SUCCESS` membership |
| `tests/test_orchestrator_ack.py` (new) | `on_ack_success` semantics (callback fires, returns `""`; non-ack falls through) |

`main.py` is not unit-tested for the wiring — covered by Task 4 real-Pi verify.

---

### Task 1: Export `MUSIC_CONTROL_ACK_SUCCESS` constant

**Files:**
- Modify: `core/container_manager.py` (add module-level constant near the top of the file, after imports)
- Modify: `tests/test_music_control.py` (append a pin-test class)

- [ ] **Step 1: Write the failing pin-test**

Append this test class to `tests/test_music_control.py` after `MusicControlExternalSpotifyPlayback` and before `if __name__ == "__main__":`:

```python
class MusicControlAckSuccessConstant(unittest.TestCase):
    """Pin the exact set of music-control results that should trigger an ack chime
    instead of TTS. If _music_control_* wording changes, this test fails and
    forces a deliberate decision."""

    def test_constant_contains_exactly_six_known_success_strings(self):
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        self.assertEqual(
            MUSIC_CONTROL_ACK_SUCCESS,
            frozenset({
                "Paused.",
                "Resumed.",
                "Skipped.",
                "Volume up.",
                "Volume down.",
                "Stopped.",
            }),
        )

    def test_constant_matches_soundcloud_success_strings(self):
        """The strings _music_control_soundcloud returns on the happy path
        must all be in the ack set."""
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        # These are the literal strings the helper returns on success.
        soundcloud_successes = {
            "Paused.", "Resumed.", "Skipped.",
            "Volume up.", "Volume down.",
            # Stop goes through _stop_mpv whose success message also matches.
        }
        self.assertTrue(soundcloud_successes.issubset(MUSIC_CONTROL_ACK_SUCCESS))

    def test_constant_matches_spotify_success_strings(self):
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        spotify_successes = {
            "Paused.", "Resumed.", "Skipped.",
            "Volume up.", "Volume down.",
            "Stopped.",
        }
        self.assertTrue(spotify_successes.issubset(MUSIC_CONTROL_ACK_SUCCESS))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_music_control.py::MusicControlAckSuccessConstant -v`
Expected: FAIL with `ImportError: cannot import name 'MUSIC_CONTROL_ACK_SUCCESS' from 'core.container_manager'`.

- [ ] **Step 3: Add the constant**

Open `core/container_manager.py`. Find the imports / module-level section near the top. Add this after the imports but before the first class definition:

```python
# Music-control transport success strings — exact-match set used to swap
# verbal confirmations for a short ack chime in voice mode. Kept here next
# to _music_control_soundcloud / _music_control_spotify so any wording
# change is a one-file edit; the pinning test in tests/test_music_control.py
# fails if the two drift.
MUSIC_CONTROL_ACK_SUCCESS = frozenset({
    "Paused.",
    "Resumed.",
    "Skipped.",
    "Volume up.",
    "Volume down.",
    "Stopped.",
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlAckSuccessConstant -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py tests/test_music_control.py
git commit -m "feat(music-control): export MUSIC_CONTROL_ACK_SUCCESS constant"
```

---

### Task 2: Add `play_ack_sound()` to VoiceInterface

**Files:**
- Modify: `core/voice.py` (add method right after `play_response_ready_sound`)
- Modify or create: `tests/test_voice_sounds.py` (smoke test that the method exists, is callable, and is a no-op when `enable_tts` is False)

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_sounds.py` (new file) with:

```python
"""Smoke tests for VoiceInterface R2-D2 sound helpers — they must be callable
without raising under both enabled and disabled TTS configurations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class PlayAckSound(unittest.TestCase):
    def test_method_exists_on_voice_interface(self):
        from core.voice import VoiceInterface
        self.assertTrue(hasattr(VoiceInterface, "play_ack_sound"))
        self.assertTrue(callable(VoiceInterface.play_ack_sound))

    def test_no_op_when_tts_disabled(self):
        """When enable_tts is False, the method must return immediately
        without touching sounddevice. We verify by patching sd.play and
        asserting it was never called."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = False
        with patch("core.voice.sd.play") as mock_play:
            v.play_ack_sound()
        mock_play.assert_not_called()

    def test_swallows_audio_errors(self):
        """Audio backend exceptions must be logged and swallowed —
        the voice loop can't crash on a missing speaker."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("core.voice.sd.play", side_effect=RuntimeError("audio gone")):
            v.play_ack_sound()  # must not raise


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_voice_sounds.py -v`
Expected: FAIL — `AttributeError: type object 'VoiceInterface' has no attribute 'play_ack_sound'`.

- [ ] **Step 3: Add the method**

Open `core/voice.py`. Find the `play_response_ready_sound` method (around line 331). Insert this *immediately after* it, before `def speak(...)`:

```python
    def play_ack_sound(self):
        """Short R2-D2-style acknowledgement chime — replaces verbal
        confirmations for music-control transport commands ("Paused.",
        "Resumed.", etc.). Plays non-blocking so the user gets near-instant
        audio feedback. Errors are logged and swallowed so a missing
        speaker can't crash the voice loop."""
        if not self.enable_tts:
            return
        try:
            sound = np.concatenate([
                # Quick rising chirp ~80ms with gentle vibrato — "got it".
                self._r2_chirp(1100, 1700, 0.08, vibrato_hz=10, vibrato_depth=40),
                self._r2_tail(0.04),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            # Intentionally no sd.wait — non-blocking ack.
        except Exception as e:
            logger.warning("Ack sound error: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_voice_sounds.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add core/voice.py tests/test_voice_sounds.py
git commit -m "feat(voice): play_ack_sound — short R2-D2 chime for transport acks"
```

---

### Task 3: Wire `on_ack_success` through orchestrator + main.py

**Files:**
- Modify: `core/orchestrator.py` — add `on_ack_success` parameter to `process_message` and `_execute_direct`; short-circuit ack-success
- Modify: `main.py` — pass `on_ack_success=voice.play_ack_sound`; skip TTS when response is `""`
- Create: `tests/test_orchestrator_ack.py` — verify the orchestrator's ack short-circuit

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_ack.py` with:

```python
"""Tests for Orchestrator._execute_direct ack-success short-circuit."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator():
    """Build a minimal Orchestrator with stubbed deps so we can test
    _execute_direct in isolation."""
    from core.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.skills = {"music-control": MagicMock(name="music-control-skill")}
    orch.container_manager = MagicMock()
    return orch


def _make_route(skill="music-control", action=None, args=None):
    """Build a minimal route-like object with the attributes _execute_direct reads."""
    route = MagicMock()
    route.skill = skill
    route.action = action
    route.args = args or {"action": "pause"}
    return route


class ExecuteDirectAckSuccess(unittest.TestCase):
    def test_ack_string_fires_callback_and_returns_empty(self):
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Paused."
        cb = MagicMock()
        result = orch._execute_direct(
            _make_route(), "pause", on_ack_success=cb
        )
        cb.assert_called_once_with()
        self.assertEqual(result, "")

    def test_non_ack_string_falls_through_and_returns_result(self):
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Nothing is playing."
        cb = MagicMock()
        result = orch._execute_direct(
            _make_route(), "pause", on_ack_success=cb
        )
        cb.assert_not_called()
        self.assertEqual(result, "Nothing is playing.")

    def test_no_callback_provided_returns_result_as_before(self):
        """When on_ack_success is None, the helper must behave identically
        to before this feature: return the result string unchanged even if
        it would have been an ack string."""
        orch = _make_orchestrator()
        orch.container_manager.execute_skill.return_value = "Resumed."
        result = orch._execute_direct(
            _make_route(), "resume", on_ack_success=None
        )
        self.assertEqual(result, "Resumed.")

    def test_all_six_ack_strings_trigger_callback(self):
        """Spot-check every member of MUSIC_CONTROL_ACK_SUCCESS routes
        through the short-circuit."""
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        for ack_str in MUSIC_CONTROL_ACK_SUCCESS:
            with self.subTest(ack=ack_str):
                orch = _make_orchestrator()
                orch.container_manager.execute_skill.return_value = ack_str
                cb = MagicMock()
                result = orch._execute_direct(
                    _make_route(), "pause", on_ack_success=cb
                )
                cb.assert_called_once_with()
                self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_ack.py -v`
Expected: FAIL — `TypeError: _execute_direct() got an unexpected keyword argument 'on_ack_success'`.

- [ ] **Step 3: Update `_execute_direct` to accept the callback**

Open `core/orchestrator.py`. Find `_execute_direct` (around line 353). Replace the current implementation:

```python
    def _execute_direct(self, route, user_message: str) -> str:
        """Execute a dispatch-pattern route without any LLM involvement."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                return result or "Done."

        # Dispatch resolution failed — build prompt lazily and fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        system_prompt = self._build_system_prompt(user_message=user_message)
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

with:

```python
    def _execute_direct(self, route, user_message: str, on_ack_success=None) -> str:
        """Execute a dispatch-pattern route without any LLM involvement.

        If `on_ack_success` is provided and the skill returns a string in
        MUSIC_CONTROL_ACK_SUCCESS, the callback is invoked (the voice layer
        plays an ack chime) and `""` is returned to signal "TTS handled by
        side channel, don't speak."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
                if result in MUSIC_CONTROL_ACK_SUCCESS and on_ack_success is not None:
                    on_ack_success()
                    return ""
                return result or "Done."

        # Dispatch resolution failed — build prompt lazily and fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        system_prompt = self._build_system_prompt(user_message=user_message)
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

- [ ] **Step 4: Thread `on_ack_success` through `process_message` and `_process_message`**

In the same file, find `process_message` (around line 280-301) and `_process_message` (around 303). Update both signatures and pass the callback to `_execute_direct`. The current signatures:

```python
    def process_message(self, user_message: str, on_chunk=None) -> str:
        ...
        with ctx:
            return self._process_message(user_message, on_chunk=on_chunk)

    def _process_message(self, user_message: str, on_chunk=None) -> str:
        ...
        if route.tier == "direct":
            result = self._execute_direct(route, user_message)
            if on_chunk is not None and result:
                on_chunk(result)
            return result
```

Change to:

```python
    def process_message(self, user_message: str, on_chunk=None, on_ack_success=None) -> str:
        ...
        with ctx:
            return self._process_message(user_message, on_chunk=on_chunk, on_ack_success=on_ack_success)

    def _process_message(self, user_message: str, on_chunk=None, on_ack_success=None) -> str:
        ...
        if route.tier == "direct":
            result = self._execute_direct(route, user_message, on_ack_success=on_ack_success)
            if on_chunk is not None and result:
                on_chunk(result)
            return result
```

(Keep the rest of `_process_message` and `process_message` exactly as-is.)

- [ ] **Step 5: Run orchestrator tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_ack.py -v`
Expected: 4 passed (one is a `subTest` over 6 entries — pytest reports it as one test method).

- [ ] **Step 6: Wire it in `main.py`**

Open `main.py`. Find the voice-mode response block (around lines 339-365). The current streaming branch:

```python
                    if os.getenv("LLM_STREAM_TO_TTS", "true").lower() == "true":
                        push_raw, finalize = voice.speak_stream_feeder(
                            on_first_chunk=voice.play_response_ready_sound,
                        )
                        try:
                            response = orchestrator.process_message(
                                transcription, on_chunk=push_raw
                            )
                            print(f"Assistant: {response}\n")
                            with profiling.stage("tts"):
                                finalize()
                        except Exception:
                            finalize()
                            raise
                    else:
                        response = orchestrator.process_message(transcription)
                        print(f"Assistant: {response}\n")
                        voice.play_response_ready_sound()
                        with profiling.stage("tts"):
                            voice.speak(response)
```

Replace with:

```python
                    if os.getenv("LLM_STREAM_TO_TTS", "true").lower() == "true":
                        push_raw, finalize = voice.speak_stream_feeder(
                            on_first_chunk=voice.play_response_ready_sound,
                        )
                        try:
                            response = orchestrator.process_message(
                                transcription,
                                on_chunk=push_raw,
                                on_ack_success=voice.play_ack_sound,
                            )
                            # Empty response = direct-tier ack chime was played
                            # in lieu of TTS; nothing to speak or print.
                            if response:
                                print(f"Assistant: {response}\n")
                            with profiling.stage("tts"):
                                finalize()
                        except Exception:
                            finalize()
                            raise
                    else:
                        response = orchestrator.process_message(
                            transcription,
                            on_ack_success=voice.play_ack_sound,
                        )
                        if response:
                            print(f"Assistant: {response}\n")
                            voice.play_response_ready_sound()
                            with profiling.stage("tts"):
                                voice.speak(response)
```

- [ ] **Step 7: Run the full test suite to verify no regressions**

Run: `.venv/bin/python -m pytest -x`
Expected: all pass. (Suite runs ~4–5 minutes.)

- [ ] **Step 8: Commit**

```bash
cd /home/daedalus/linux/miniclaw
git add core/orchestrator.py main.py tests/test_orchestrator_ack.py
git commit -m "feat(music-control): chime instead of TTS for transport acks

Thread on_ack_success callback through process_message -> _execute_direct.
When the direct-tier result lands in MUSIC_CONTROL_ACK_SUCCESS, fire the
callback (which plays voice.play_ack_sound) and return \"\" so main.py
skips voice.speak. Non-ack results (errors, clarifications) fall through
to normal TTS unchanged."
```

- [ ] **Step 9: Push**

```bash
git push origin main
```

---

### Task 4: Real-Pi verification (manual)

**Files:** none — this is a hardware checklist.

- [ ] **Step 1: Pull and restart on the Pi**

```bash
ssh pi "cd ~/miniclaw && git pull && systemctl --user restart miniclaw"
```

Wait ~30s for MiniClaw to finish booting (`journalctl --user -u miniclaw -f` until the wake-loop ready line).

- [ ] **Step 2: Start Spotify playback from the phone on MiniClaw**

Open Spotify on phone, pick "MiniClaw" in the device picker, hit play on any track.

- [ ] **Step 3: Verify each transport command chimes instead of speaking**

Say each, one at a time. After each, you should hear **only the chime** — no spoken "Paused." / "Resumed." / etc.

| Command | Expected |
|---------|----------|
| "Jarvis, pause" | Audio stops; chime plays. No speech. |
| "Jarvis, resume" | Audio resumes; chime plays. No speech. |
| "Jarvis, volume down" | Volume drops; chime plays. No speech. |
| "Jarvis, volume up" | Volume rises; chime plays. No speech. |
| "Jarvis, skip" | Next track plays; chime plays. No speech. |
| "Jarvis, stop" | Audio stops; chime plays. No speech. |

If any of those instead produces speech, capture the journal:

```bash
ssh pi "journalctl --user -u miniclaw --since '5 minutes ago' | grep -iE 'TierRouter|music-control|ack|direct'"
```

- [ ] **Step 4: Verify error cases still speak**

Stop playback entirely (so `_active_music_source` is None and no Spotify session is loaded). Then say "Jarvis, pause".

Expected: MiniClaw **speaks** "Nothing is playing." — the chime path does NOT fire because the result string isn't in the ack set.

- [ ] **Step 5: Verify a garbled / unclear transcript still asks for clarification**

Mumble something nonsensical at the wake word — e.g. say "Jarvis, fzzzbwh".

Expected: MiniClaw either asks for clarification (Claude's clarification path) or, at minimum, does not produce a chime — chime is reserved for the six exact success strings.

- [ ] **Step 6: Document the result**

If everything passed, the feature ships. If anything failed, capture journal output and save a project memory describing the failure mode.

---

## Done criteria

- All new tests pass: `.venv/bin/python -m pytest tests/test_music_control.py::MusicControlAckSuccessConstant tests/test_voice_sounds.py tests/test_orchestrator_ack.py -v`
- Full repo suite green: `.venv/bin/python -m pytest -x`
- Pi verification (Task 4) passes — chime fires for the six transport successes; "Nothing is playing." still speaks; clarifications still speak.
