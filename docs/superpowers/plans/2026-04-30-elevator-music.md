# Elevator Music Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the silent post-listen wait with a looping elevator-music WAV that starts when the user finishes speaking and hard-cuts when the response is ready.

**Architecture:** Add `start_thinking_music()` / `stop_thinking_music()` to `core/voice.py`. The asset is loaded once at `Voice.__init__` from `assets/elevator.wav`, decoded with stdlib `wave`, downmixed to mono if stereo, resampled to the output device's rate, and cached as a `numpy.float32` array. Start spawns a daemon thread that loops `sd.play(buf) ; sd.wait()` while a flag is set. Stop clears the flag, calls `sd.stop()`, joins the thread. `main.py` wraps `orchestrator.process_message` with a `start … try … finally stop` pattern so the music always stops, even on exceptions.

**Tech Stack:** Python 3.12, stdlib `wave` + `numpy` for decoding, `sounddevice` for playback (already a project dep), `threading.Thread` daemon for the loop, pytest with `monkeypatch` for tests.

**Spec:** `docs/superpowers/specs/2026-04-30-elevator-music-design.md`

---

## File Structure

- **new:** `assets/elevator.wav` — bundled audio asset, ~10–30 s, 16-bit PCM
- **new:** `tests/test_voice_elevator_music.py` — unit tests with stubbed `sounddevice`
- **edit:** `core/voice.py` — `_MUSIC_ASSET_PATH` constant, `_load_music_buffer` helper, init wiring, `start_thinking_music`, `stop_thinking_music`, `_music_loop`
- **edit:** `main.py` — voice-loop replaces `play_thinking_sound` with `start_thinking_music` / `try / finally stop_thinking_music`
- **edit:** `README.md` — attribution line **only if** the sourced clip is CC-BY (not CC0)

---

## Task 1: Source the asset file

**Files:**
- Create: `assets/elevator.wav`
- Modify (conditionally): `README.md`

This task is research, not code. It is on the critical path — without an audio file the feature does nothing — but the code in tasks 2–4 can still be written and tested first because the missing-asset path is one of the test cases. Source the file before merging to main.

- [ ] **Step 1: Search for a CC0 / public-domain elevator-style clip**

Look in this order:
1. https://freesound.org/ with the CC0 license filter and search terms `elevator music`, `muzak`, `hold music`, `lounge`.
2. https://opengameart.org/ — filter by Public Domain (CC0).
3. https://archive.org/ — public-domain music collections.
4. https://incompetech.com/ — Kevin MacLeod's library is mostly CC-BY (not CC0). Acceptable as a fallback.

Aim for ~10–30 seconds. Looping sounds best with a short clip that has no abrupt edits.

- [ ] **Step 2: Verify the format**

The decoder in Task 2 supports:
- 16-bit PCM (`sampwidth == 2`)
- Mono or stereo (stereo will be downmixed)
- Any sample rate (resampled to the device rate)

If the source is MP3 / OGG / 24-bit / 32-bit-float, convert it. ffmpeg one-liner:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 44100 -sample_fmt s16 assets/elevator.wav
```

(Drop the `-ac 1` if you want to keep stereo — the decoder will downmix at runtime.)

- [ ] **Step 3: Confirm it sounds correct**

```bash
aplay assets/elevator.wav   # or 'afplay' on macOS
```

It should be obviously elevator-music, loop pleasantly, and not have a click or pop at the boundaries.

- [ ] **Step 4: Check the file size**

```bash
ls -lh assets/elevator.wav
```

Target under 5 MB. If it's larger, shorten the clip with ffmpeg `-t 30`.

- [ ] **Step 5: If CC-BY, add attribution to README.md**

Append a section at the bottom of `README.md`:

```markdown
## Audio Attribution

`assets/elevator.wav` — "<Track Title>" by <Artist>,
<source URL>, licensed under CC-BY 4.0.
```

Skip this step if the clip is CC0 / public-domain.

- [ ] **Step 6: Commit**

```bash
git add assets/elevator.wav
# Add README.md only if attribution was added in step 5.
git commit -m "feat(voice): bundle elevator-music asset

Adds assets/elevator.wav for the elevator-music feature
(spec: docs/superpowers/specs/2026-04-30-elevator-music-design.md).
Source: <URL>, license: <CC0|CC-BY 4.0>."
```

**If no acceptable clip can be found:** stop here and ask the user to provide one. Do not move on to Task 2 without an asset OR an explicit decision to defer asset sourcing.

---

## Task 2: WAV loader + env/tts gates in Voice.__init__

**Files:**
- Modify: `core/voice.py`
- Create: `tests/test_voice_elevator_music.py`
- Test fixture: a tiny in-test WAV created with stdlib `wave` (no external fixture file needed)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_elevator_music.py`:

```python
"""Unit tests for the elevator-music feature on core.voice.Voice."""

import logging
import threading
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import sounddevice as sd

from core import voice as voice_module


@pytest.fixture
def fake_wav(tmp_path: Path) -> Path:
    """Write a 0.1-second 16-bit mono 44.1kHz WAV for tests."""
    path = tmp_path / "elevator.wav"
    samples = (np.zeros(4410, dtype=np.int16)).tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(samples)
    return path


@pytest.fixture
def voice_factory(monkeypatch, fake_wav):
    """Build a Voice instance with audio output safely stubbed.

    Stubs:
      - resolve_input_device / resolve_output_device → fake indices
      - output_samplerate → 48000
      - WhisperBackend / KokoroTTSBackend → MagicMock so init doesn't
        try to load real models
      - pyaudio.PyAudio → MagicMock so no microphone is opened
    """
    monkeypatch.setattr(voice_module, "_MUSIC_ASSET_PATH", fake_wav)
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)

    def _make(**overrides):
        kwargs = dict(enable_tts=True)
        kwargs.update(overrides)
        return voice_module.VoiceInterface(**kwargs)

    return _make


def test_disabled_by_env_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "false")
    sd_play = MagicMock()
    monkeypatch.setattr(sd, "play", sd_play)
    v = voice_factory()
    v.start_thinking_music()
    assert v._music_thread is None
    assert sd_play.call_count == 0


def test_tts_disabled_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    sd_play = MagicMock()
    monkeypatch.setattr(sd, "play", sd_play)
    v = voice_factory(enable_tts=False)
    v.start_thinking_music()
    assert v._music_thread is None
    assert sd_play.call_count == 0


def test_missing_asset_logs_and_disables(monkeypatch, voice_factory, caplog, tmp_path):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    monkeypatch.setattr(voice_module, "_MUSIC_ASSET_PATH", tmp_path / "nope.wav")
    with caplog.at_level(logging.WARNING, logger="core.voice"):
        v = voice_factory()
    assert v._music_buffer is None
    assert any("elevator" in rec.message.lower() for rec in caplog.records)
    # start is a safe no-op
    v.start_thinking_music()
    assert v._music_thread is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_voice_elevator_music.py -v
```

Expected: 3 failures with `AttributeError: ... has no attribute '_MUSIC_ASSET_PATH'` (or similar) — the constant and methods don't exist yet.

- [ ] **Step 3: Add the imports and asset-path constant in `core/voice.py`**

At the top of `core/voice.py`, add `threading` and `Path` imports if missing:

```python
import os
import wave
import tempfile
import logging
import threading
from pathlib import Path

import numpy as np
import pyaudio
import sounddevice as sd
```

After the existing imports and `logger = logging.getLogger(__name__)` line, add:

```python
_MUSIC_ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "elevator.wav"
```

- [ ] **Step 4: Add the `_load_music_buffer` helper and init wiring**

Find `VoiceInterface.__init__` (the class is named `VoiceInterface` in `core/voice.py`). At the end of `__init__` — after the existing setup — add:

```python
        # Elevator-music feature state
        self._music_playing = False
        self._music_thread: threading.Thread | None = None
        self._music_enabled = (
            os.environ.get("MINICLAW_ELEVATOR_MUSIC", "true").strip().lower() != "false"
        )
        if self._music_enabled and self.enable_tts:
            self._music_buffer = self._load_music_buffer()
        else:
            self._music_buffer = None
```

Then add a new private method on `Voice` (anywhere reasonable — near the other private helpers):

```python
    def _load_music_buffer(self) -> "np.ndarray | None":
        """Load and prepare assets/elevator.wav for looped playback.

        Returns a float32 mono numpy array at the output device's sample
        rate, or None if the file is missing / unreadable / unsupported.
        Failures are non-fatal: the elevator-music feature silently
        disables itself for the session.
        """
        path = _MUSIC_ASSET_PATH
        try:
            with wave.open(str(path), "rb") as wf:
                n_channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                sample_width = wf.getsampwidth()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
        except (FileNotFoundError, wave.Error, OSError) as exc:
            logger.warning("Elevator music disabled — could not load %s: %s", path, exc)
            return None

        if sample_width != 2:
            logger.warning(
                "Elevator music disabled — %s must be 16-bit PCM (got %d-bit)",
                path,
                sample_width * 8,
            )
            return None

        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        elif n_channels != 1:
            logger.warning(
                "Elevator music disabled — %s has %d channels (need 1 or 2)",
                path,
                n_channels,
            )
            return None

        if sample_rate != self._output_samplerate:
            data = resample(data, sample_rate, self._output_samplerate)
        return data.astype(np.float32, copy=False)
```

- [ ] **Step 5: Run tests to verify the gate/missing-asset tests pass**

```bash
.venv/bin/python -m pytest tests/test_voice_elevator_music.py::test_disabled_by_env_is_noop tests/test_voice_elevator_music.py::test_tts_disabled_is_noop tests/test_voice_elevator_music.py::test_missing_asset_logs_and_disables -v
```

Expected: 3 passed.

These tests don't yet require `start_thinking_music` to *do* anything — they only assert the no-op behavior, which is already true while the method body is just `pass`. To get them to pass, you must add a trivial `start_thinking_music` stub now:

```python
    def start_thinking_music(self) -> None:
        """Start looping elevator music in a background thread (no-op
        when disabled, when TTS is off, or when the asset is missing)."""
        if not self.enable_tts or self._music_buffer is None:
            return
        # Real implementation lands in Task 3.
```

Re-run; expect 3 passed.

- [ ] **Step 6: Run the full suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 363 passed (existing 360 + 3 new).

- [ ] **Step 7: Commit**

```bash
git add core/voice.py tests/test_voice_elevator_music.py
git commit -m "feat(voice): elevator-music asset loader and env gates

Adds _MUSIC_ASSET_PATH constant, _load_music_buffer helper that
decodes a 16-bit PCM WAV (mono or stereo, any sample rate) into a
float32 mono buffer at the output device rate, and the
MINICLAW_ELEVATOR_MUSIC env gate. Failures are non-fatal: the
feature silently disables for the session. start_thinking_music is
a stub; the real loop lands in the next commit."
```

---

## Task 3: Implement start/stop and the _music_loop

**Files:**
- Modify: `core/voice.py`
- Modify: `tests/test_voice_elevator_music.py`

- [ ] **Step 1: Append three more failing tests**

Add to the bottom of `tests/test_voice_elevator_music.py`:

```python
def test_start_then_stop_no_raise(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    play_event = threading.Event()
    stop_called = threading.Event()

    def fake_play(*args, **kwargs):
        play_event.set()

    def fake_wait():
        # Block until sd.stop() is called or the test times out.
        stop_called.wait(timeout=1.0)

    def fake_stop():
        stop_called.set()

    monkeypatch.setattr(sd, "play", fake_play)
    monkeypatch.setattr(sd, "wait", fake_wait)
    monkeypatch.setattr(sd, "stop", fake_stop)

    v = voice_factory()
    v.start_thinking_music()
    assert play_event.wait(timeout=1.0), "sd.play was never called"
    v.stop_thinking_music()
    # Thread must have exited.
    assert v._music_thread is None or not v._music_thread.is_alive()


def test_stop_without_start_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    sd_stop = MagicMock()
    monkeypatch.setattr(sd, "stop", sd_stop)
    v = voice_factory()
    v.stop_thinking_music()  # never started
    assert sd_stop.call_count == 0


def test_double_start_only_spawns_one_thread(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    stop_called = threading.Event()

    monkeypatch.setattr(sd, "play", lambda *a, **kw: None)
    monkeypatch.setattr(sd, "wait", lambda: stop_called.wait(timeout=1.0))
    monkeypatch.setattr(sd, "stop", lambda: stop_called.set())

    v = voice_factory()
    v.start_thinking_music()
    first = v._music_thread
    v.start_thinking_music()
    assert v._music_thread is first
    v.stop_thinking_music()
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_voice_elevator_music.py -v
```

Expected: 3 newly-added tests fail with `AttributeError` for `stop_thinking_music` (or because `start_thinking_music` is still a stub that does nothing).

- [ ] **Step 3: Replace the stub `start_thinking_music` and add `_music_loop` and `stop_thinking_music`**

Replace the stub from Task 2 with the real method, and add the loop and stop method:

```python
    def start_thinking_music(self) -> None:
        """Start looping elevator music in a background daemon thread.

        No-op when TTS is disabled, the asset failed to load, or a
        music thread is already alive.
        """
        if not self.enable_tts or self._music_buffer is None:
            return
        if self._music_thread is not None and self._music_thread.is_alive():
            return
        self._music_playing = True
        self._music_thread = threading.Thread(
            target=self._music_loop, daemon=True, name="elevator-music"
        )
        self._music_thread.start()

    def _music_loop(self) -> None:
        while self._music_playing:
            try:
                sd.play(
                    self._music_buffer,
                    samplerate=self._output_samplerate,
                    device=self._output_device_index,
                )
                sd.wait()
            except Exception as exc:
                logger.warning("Elevator music error: %s", exc)
                return

    def stop_thinking_music(self) -> None:
        """Hard-stop the music loop. Idempotent."""
        if not self._music_playing:
            return
        self._music_playing = False
        try:
            sd.stop()
        except Exception as exc:
            logger.warning("Elevator music stop error: %s", exc)
        if self._music_thread is not None:
            self._music_thread.join(timeout=0.5)
            self._music_thread = None
```

- [ ] **Step 4: Run the elevator-music tests**

```bash
.venv/bin/python -m pytest tests/test_voice_elevator_music.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 366 passed (existing 360 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add core/voice.py tests/test_voice_elevator_music.py
git commit -m "feat(voice): start/stop elevator music loop

Spawns a daemon thread that loops sd.play(buf) ; sd.wait() while
_music_playing is set. stop_thinking_music clears the flag, calls
sd.stop, joins the thread (0.5s timeout). Idempotent on stop and
guarded against double-start."
```

---

## Task 4: Wire into main.py voice loop

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read the current voice-loop block**

```bash
grep -n "play_thinking_sound\|process_message\|profiling.turn" main.py | head
```

The block to edit is inside the `with profiling.turn():` scope added in the profiling plan, around the lines:

```python
voice.play_thinking_sound()
response = orchestrator.process_message(transcription)
print(f"Assistant: {response}\n")
with profiling.stage("tts"):
    voice.speak(response)
```

- [ ] **Step 2: Replace with the start / try / finally / stop pattern**

```python
voice.start_thinking_music()
try:
    response = orchestrator.process_message(transcription)
finally:
    voice.stop_thinking_music()
print(f"Assistant: {response}\n")
with profiling.stage("tts"):
    voice.speak(response)
```

The `try/finally` guarantees the music stops even if `process_message` raises (KeyboardInterrupt, network error, etc.).

`voice.play_thinking_sound` is no longer called from `main.py` but stays defined on `Voice` — out-of-scope to delete.

- [ ] **Step 3: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 366 passed.

- [ ] **Step 4: Syntax-check main.py**

```bash
.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); print('main.py parses')"
```

Expected: `main.py parses`

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(voice): wire elevator music into voice loop

Replaces the blocking play_thinking_sound() call with
start_thinking_music() before orchestrator.process_message and
stop_thinking_music() in a finally block, so the music always
stops even if process_message raises."
```

---

## Task 5: Manual Pi validation

**Files:** none (operational task)

- [ ] **Step 1: Rsync code + asset to the Pi**

```bash
rsync -av --relative \
  core/voice.py \
  main.py \
  tests/test_voice_elevator_music.py \
  assets/elevator.wav \
  pi:/home/archimedes/miniclaw/
```

- [ ] **Step 2: Run the unit tests on the Pi**

```bash
ssh pi 'cd ~/miniclaw && .venv/bin/python -m pytest tests/test_voice_elevator_music.py -q'
```

Expected: 6 passed.

- [ ] **Step 3: Confirm the env var state**

```bash
ssh pi 'grep "^MINICLAW_ELEVATOR_MUSIC" ~/miniclaw/.env || echo "not set (default true)"'
```

Either explicitly set to `true` or absent — both enable the feature.

- [ ] **Step 4: Restart MiniClaw on the Pi**

Use the existing run procedure (kill the current process, re-launch `./run.sh --voice` under tmux/systemd/whatever).

- [ ] **Step 5: Voice-test the round trip**

1. Say the wake word, then ask anything that takes ≥1 second to answer (a tool call is ideal — e.g. `"computer, what's the weather"`).
2. Confirm: music starts immediately after you stop talking, plays during the wait, hard-stops the moment Kokoro begins speaking.
3. Repeat with a quick direct route (`"computer, what time is it"`) — music may barely play at all if the response is sub-second; that's expected and fine.
4. Optionally, say `MINICLAW_ELEVATOR_MUSIC=false` in `.env`, restart, confirm the wait is silent (and there's no R2 chirp either — the chirp was removed from the loop).

- [ ] **Step 6: Capture a `[TIMING-SUMMARY]` line for the music turn**

```bash
ssh pi 'grep TIMING-SUMMARY ~/miniclaw/miniclaw.log | tail -n 3'
```

The summary should still look the same as before (no music stage is added — music runs on a parallel thread, not on the user-perceived critical path).

- [ ] **Step 7: No commit**

This task validates the feature on the device. Move on to brainstorming actual latency fixes once you have the timing-summary lines.

---

## Self-Review

**Spec coverage:** every requirement in the spec maps to a task.

| Spec section                                       | Task |
|----------------------------------------------------|------|
| Decisions: WAV file at `assets/elevator.wav`       | 1    |
| Decisions: 16-bit PCM, 44.1 kHz, mono              | 1    |
| Decisions: license (CC0 preferred, CC-BY fallback) | 1    |
| Decisions: hard cut                                | 3 (`stop_thinking_music`) |
| Decisions: chirp removed                           | 4    |
| Decisions: default ON, env-var off                 | 2    |
| Decisions: silent fallback on failure              | 2 (`_load_music_buffer`) |
| Architecture: start/stop methods on Voice          | 3    |
| Architecture: daemon thread + flag                 | 3    |
| Components: `_MUSIC_ASSET_PATH` constant           | 2    |
| Components: `Voice.__init__` additions             | 2    |
| Components: `_music_loop`                          | 3    |
| Components: `assets/elevator.wav`                  | 1    |
| Components: README attribution if CC-BY            | 1    |
| Data flow: `try/finally` in main.py                | 4    |
| Error handling: env-disabled / tts-disabled        | 2 (gates), 3 (start no-ops on `_music_buffer is None`) |
| Error handling: missing asset                      | 2    |
| Error handling: sd.play / sd.stop raises           | 3 (`_music_loop` and `stop_thinking_music` swallow + log) |
| Error handling: process_message raises             | 4 (`try/finally`) |
| Testing: 6 unit tests                              | 2 (3 tests) and 3 (3 tests) |
| Out of scope: volume, fade, pool, path-override    | not implemented (correct) |

**Placeholder scan:** every code-changing step shows complete code. The only research task (Task 1) explicitly enumerates sources and includes a "stop and ask" exit when nothing CC0/CC-BY usable is found.

**Type / signature consistency:**
- `_MUSIC_ASSET_PATH: Path` — defined in Task 2, referenced in Task 2 (init) and Task 2 test (monkeypatch).
- `_music_buffer: np.ndarray | None` — set in Task 2 init, read in Task 2 stub and Task 3 real method.
- `_music_playing: bool`, `_music_thread: threading.Thread | None` — initialized in Task 2, read/written in Task 3.
- `_load_music_buffer()` returns `np.ndarray | None` and the call site in init handles `None`.
- `start_thinking_music`, `stop_thinking_music` signatures match between the stub (Task 2) and full implementation (Task 3).
- `main.py` calls in Task 4 match those defined in Tasks 2/3.

**Note on existing audio-out tests:** there is no `tests/test_voice.py` for actual playback today (audio output is hardware-dependent and skipped from CI). The new test file follows the project pattern of mocking `sounddevice` rather than playing real audio.
