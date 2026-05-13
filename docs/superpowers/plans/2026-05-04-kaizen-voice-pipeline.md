# Kaizen Voice Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Kaizen's voice loop accuracy and perceived latency on Pi 5 by swapping in `openWakeWord`, Silero VAD, `faster-whisper`, and Ollama→Kokoro streaming. Each swap ships independently behind a `.env` flag with a fallback to today's behaviour.

**Architecture:** Three Protocols in `core/voice_backends.py` — `WakeBackend`, `VadBackend`, `SttBackend` — each with a primary impl and a fallback wrapping today's behaviour. Factory functions select implementations from `.env`. Streaming wires through `OllamaToolLoop` → `Orchestrator.process_message_stream` → `KokoroTTSBackend.speak_stream`. Hailo path is preserved at `base`; CPU path moves to `small`.

**Tech Stack:** Python 3.11+, `openwakeword`, `silero-vad` (onnxruntime), `faster-whisper` (ctranslate2), Kokoro (existing), pytest + unittest.TestCase, ollama (existing), Anthropic SDK (existing).

**Spec:** `docs/superpowers/specs/2026-05-04-kaizen-voice-pipeline-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `core/voice_backends.py` | Modify | Add `WakeBackend` and `VadBackend` Protocols + 4 new backends; add `FasterWhisperBackend`; add `build_wake_backend` and `build_vad_backend` factories; split CPU/Hailo Whisper model selection in `build_stt_backend` |
| `core/voice.py` | Modify | `wait_for_wake_word()` consumes a `WakeBackend`; `_record_until_silence()` consumes a `VadBackend`; new `KokoroTTSBackend.speak_stream` is consumed via a feeder helper |
| `core/ollama_tool_loop.py` | Modify | Flip `"stream": False` → `True`, parse line-delimited JSON deltas, expose deltas via callback |
| `core/orchestrator.py` | Modify | Add `process_message_stream(user_message, on_chunk)` |
| `main.py` | Modify | Load new env vars; build new backends via factories; voice loop calls `process_message_stream` |
| `requirements.txt` | Modify | Add `openwakeword`, `silero-vad`, `faster-whisper` |
| `.env.example` | Modify | Document new keys, mark legacy keys as fallback-only |
| `tests/test_voice_backends.py` | Modify | Add tests for new Protocols, backends, factories, fallbacks |
| `tests/test_ollama_tool_loop.py` | Modify | Add streaming-mode tests |
| `tests/test_orchestrator_streaming.py` | Create | Tests for `process_message_stream` |
| `tests/test_kokoro_stream.py` | Create | Tests for sentence-flush logic in `speak_stream` |

**Test command (used at every step):** `.venv/bin/python -m pytest tests/<file>::<TestClass>::<test_name> -v`
**Full suite at wave boundaries:** `./scripts/test.sh`

---

## Wave 1 — openWakeWord (replaces Whisper-tiny wake stream)

### Task 1.1: Add `openwakeword` to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:
```
# Wake word
openwakeword>=0.6.0
```

- [ ] **Step 2: Install in venv**

Run: `.venv/bin/pip install openwakeword`
Expected: Successful install. Some platforms require `tflite-runtime`; if pip emits a warning, accept it — fallback exists.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat(deps): add openwakeword for wake detection"
```

### Task 1.2: Define `WakeBackend` Protocol + `WhisperWakeBackend` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
import numpy as np
from unittest.mock import MagicMock


class WhisperWakeBackendTests(unittest.TestCase):
    @patch("core.voice_backends.whisper.load_model")
    def test_detect_returns_true_when_phrase_present(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "hello computer hi"}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        audio = np.zeros(16000, dtype=np.float32)

        self.assertTrue(backend.detect(audio))

    @patch("core.voice_backends.whisper.load_model")
    def test_detect_returns_false_when_phrase_absent(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "hello world"}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        audio = np.zeros(16000, dtype=np.float32)

        self.assertFalse(backend.detect(audio))

    @patch("core.voice_backends.whisper.load_model")
    def test_detect_normalises_case_and_whitespace(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "  Computer.  "}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        self.assertTrue(backend.detect(np.zeros(16000, dtype=np.float32)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::WhisperWakeBackendTests -v`
Expected: 3 FAILs with `AttributeError: module 'core.voice_backends' has no attribute 'WhisperWakeBackend'`.

- [ ] **Step 3: Implement Protocol + class**

In `core/voice_backends.py`, after the existing `SttBackend` Protocol, add:
```python
import numpy as np


class WakeBackend(Protocol):
    """Continuous wake-word detector. Consumes audio chunks, returns trigger bool."""
    def detect(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class WhisperWakeBackend:
    """Fallback wake backend — runs Whisper-tiny on a 2s window and substring-matches.

    Preserves current Kaizen behaviour. Used when WAKE_BACKEND=whisper or when
    openWakeWord fails to load.
    """

    def __init__(self, model_name: str = "tiny", wake_phrase: str = "computer"):
        logger.info("Loading Whisper wake model: %s", model_name)
        self.model = whisper.load_model(model_name)
        self.wake_phrase = wake_phrase.lower().strip()

    def detect(self, audio_chunk: np.ndarray) -> bool:
        result = self.model.transcribe(audio_chunk, language="en", fp16=False)
        transcript = result["text"].lower().strip()
        return self.wake_phrase in transcript

    def reset(self) -> None:
        # Whisper is stateless per call; nothing to reset.
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::WhisperWakeBackendTests -v`
Expected: 3 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add WakeBackend protocol + WhisperWakeBackend fallback"
```

### Task 1.3: Implement `OpenWakeWordBackend` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

**API note (openwakeword 0.4.0):** the `Model` constructor takes `wakeword_model_paths` (file paths) rather than `wakeword_models` (names). Path resolution is via `openwakeword.models[name]["model_path"]`. The score dict keys are version-suffixed (e.g. `"hey_jarvis_v0.1"`), not bare names. The backend takes a friendly `model_name` ("hey_jarvis") and handles both translations internally so callers stay clean.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class OpenWakeWordBackendTests(unittest.TestCase):
    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_true_when_score_exceeds_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.85}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        audio = np.zeros(1280, dtype=np.float32)

        self.assertTrue(backend.detect(audio))
        mock_owww.Model.assert_called_once_with(
            wakeword_model_paths=["/fake/hey_jarvis_v0.1.onnx"]
        )

    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_false_when_score_below_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.3}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        self.assertFalse(backend.detect(np.zeros(1280, dtype=np.float32)))

    @patch("core.voice_backends.openwakeword")
    def test_init_raises_for_unknown_model_name(self, mock_owww):
        mock_owww.models = {"hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}}
        with self.assertRaises(ValueError):
            voice_backends.OpenWakeWordBackend(
                model_name="not_a_real_model", threshold=0.5
            )

    @patch("core.voice_backends.openwakeword")
    def test_reset_clears_model_state(self, mock_owww):
        mock_model = MagicMock()
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        backend.reset()

        mock_model.reset.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::OpenWakeWordBackendTests -v`
Expected: 4 FAILs with `AttributeError: module 'core.voice_backends' has no attribute 'OpenWakeWordBackend'`.

- [ ] **Step 3: Implement the class**

In `core/voice_backends.py`, after `WhisperWakeBackend`, add:
```python
try:
    import openwakeword
    _OPENWAKEWORD_AVAILABLE = True
except ImportError:
    openwakeword = None  # type: ignore[assignment]
    _OPENWAKEWORD_AVAILABLE = False


class OpenWakeWordBackend:
    """Primary wake backend — purpose-built keyword spotter.

    Expects ~80ms audio chunks at 16kHz int16 or float32. Returns True when
    the model's score for `model_name` crosses `threshold`.

    `model_name` accepts canonical openwakeword names ("hey_jarvis", "alexa",
    "hey_mycroft", "timer", "weather"). The backend resolves to the bundled
    ONNX path and to the version-suffixed score-dict key automatically.
    """

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        if not _OPENWAKEWORD_AVAILABLE:
            raise ImportError("openwakeword not installed")
        if model_name not in openwakeword.models:
            raise ValueError(
                f"unknown openwakeword model {model_name!r}; "
                f"available: {list(openwakeword.models)}"
            )

        logger.info("Loading openWakeWord model: %s", model_name)
        self.model_name = model_name
        self.threshold = threshold

        meta = openwakeword.models[model_name]
        model_path = meta["model_path"]
        # Score-dict key is the bundled filename without extension (e.g. "hey_jarvis_v0.1").
        self._score_key = Path(model_path).stem

        self.model = openwakeword.Model(wakeword_model_paths=[model_path])

    def detect(self, audio_chunk: np.ndarray) -> bool:
        scores = self.model.predict(audio_chunk)
        score = scores.get(self._score_key, 0.0)
        return score >= self.threshold

    def reset(self) -> None:
        self.model.reset()
```

(`Path` is already imported at the top of `voice_backends.py`. If it isn't, add `from pathlib import Path`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::OpenWakeWordBackendTests -v`
Expected: 4 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add OpenWakeWordBackend keyword spotter"
```

### Task 1.4: Add `build_wake_backend` factory with fallback (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class BuildWakeBackendTests(unittest.TestCase):
    @patch("core.voice_backends.OpenWakeWordBackend")
    def test_returns_openwakeword_when_backend_is_openwakeword(self, mock_owww):
        instance = object()
        mock_owww.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="openwakeword",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("openwakeword", message)
        self.assertIn("hey_jarvis", message)

    @patch("core.voice_backends.WhisperWakeBackend")
    def test_returns_whisper_when_backend_is_whisper(self, mock_whisper):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="whisper",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("whisper", message)
        self.assertIn("computer", message)

    @patch("core.voice_backends.WhisperWakeBackend")
    @patch(
        "core.voice_backends.OpenWakeWordBackend",
        side_effect=ImportError("openwakeword not installed"),
    )
    def test_falls_back_to_whisper_on_openwakeword_failure(
        self, mock_owww, mock_whisper
    ):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="openwakeword",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("fallback", message.lower())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::BuildWakeBackendTests -v`
Expected: 3 FAILs with `AttributeError: module 'core.voice_backends' has no attribute 'build_wake_backend'`.

- [ ] **Step 3: Implement the factory**

In `core/voice_backends.py`, after `OpenWakeWordBackend`, add:
```python
def build_wake_backend(
    backend_name: str,
    model_name: str,
    threshold: float,
    wake_phrase: str,
    whisper_model: str,
) -> tuple[WakeBackend, str]:
    """Select wake backend by name with automatic fallback to Whisper."""
    if backend_name == "openwakeword":
        try:
            backend = OpenWakeWordBackend(model_name=model_name, threshold=threshold)
            return backend, f"Wake backend: openwakeword ({model_name}, threshold={threshold})"
        except (ImportError, Exception) as exc:
            logger.warning("openWakeWord unavailable (%s) — falling back to Whisper wake", exc)
            backend = WhisperWakeBackend(
                model_name=whisper_model, wake_phrase=wake_phrase
            )
            return backend, f"Wake backend: whisper:{whisper_model} ('{wake_phrase}') — openwakeword fallback"

    backend = WhisperWakeBackend(model_name=whisper_model, wake_phrase=wake_phrase)
    return backend, f"Wake backend: whisper:{whisper_model} ('{wake_phrase}')"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::BuildWakeBackendTests -v`
Expected: 3 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add build_wake_backend factory with whisper fallback"
```

### Task 1.5: Wire `wait_for_wake_word` to consume `WakeBackend`

**Files:**
- Modify: `core/voice.py`
- Modify: `tests/test_main_voice_backend_selection.py` (or create dedicated test if absent — see Step 1)

- [ ] **Step 1: Read existing wait_for_wake_word context**

Read `core/voice.py` lines 212–280. Note: it currently calls `self.stt_backend.transcribe_wake_audio(audio_float)` and substring-matches. We'll refactor to delegate the entire detection decision to a new `self.wake_backend`.

- [ ] **Step 2: Update VoiceInterface constructor**

In `core/voice.py`, update `__init__` to accept `wake_backend`:
```python
    def __init__(
        self,
        whisper_model: str = "base",
        wake_model: str = "tiny",
        wake_phrase: str = "computer",
        ...
        stt_backend=None,
        wake_backend=None,
        tts_backend=None,
    ):
        ...
        self.stt_backend = stt_backend or WhisperBackend(
            wake_model=wake_model,
            transcription_model=whisper_model,
        )
        self.wake_backend = wake_backend  # may be None for legacy callers
```

- [ ] **Step 3: Refactor wait_for_wake_word**

Replace the existing wake-detection inner loop with a backend call. Inside `wait_for_wake_word`:
```python
            try:
                audio_float = (
                    np.frombuffer(b"".join(audio_buffer), dtype=np.int16).astype(
                        np.float32
                    )
                    / 32768.0
                )

                if self.wake_backend is not None:
                    detected = self.wake_backend.detect(audio_float)
                else:
                    transcript = self.stt_backend.transcribe_wake_audio(audio_float)
                    detected = self.wake_phrase in transcript

                if detected:
                    logger.info("Wake detected")
                    self._shared_stream = stream
                    return True
```

(Keep the existing buffer-management and stream lifecycle code unchanged. Only the detection step changes.)

- [ ] **Step 4: Add an integration test**

Append to `tests/test_main_voice_backend_selection.py` (or create `tests/test_voice_wake_integration.py`):
```python
import unittest
from unittest.mock import MagicMock, patch

class VoiceWakeBackendIntegrationTests(unittest.TestCase):
    @patch("core.voice.pyaudio.PyAudio")
    def test_wait_for_wake_word_uses_wake_backend_when_provided(self, mock_pa):
        from core.voice import VoiceInterface

        wake_backend = MagicMock()
        wake_backend.detect.side_effect = [False, False, True]
        stt_backend = MagicMock()
        tts_backend = MagicMock()

        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\x00" * 32000  # 1s silence at 16kHz mono int16
        mock_pa.return_value.open.return_value = mock_stream

        voice = VoiceInterface(
            stt_backend=stt_backend,
            wake_backend=wake_backend,
            tts_backend=tts_backend,
            enable_tts=False,
        )

        result = voice.wait_for_wake_word()

        self.assertTrue(result)
        self.assertEqual(wake_backend.detect.call_count, 3)
        stt_backend.transcribe_wake_audio.assert_not_called()
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`
Expected: PASS for all tests including the new one.

- [ ] **Step 6: Commit**

```bash
git add core/voice.py tests/test_main_voice_backend_selection.py
git commit -m "feat(voice): wait_for_wake_word delegates to WakeBackend"
```

### Task 1.6: Wire env vars + factory in `main.py`

**Files:**
- Modify: `main.py`
- Modify: `.env.example`

- [ ] **Step 1: Read main.py voice setup**

Read `main.py` lines 85–130. Note env loading and `VoiceInterface` construction.

- [ ] **Step 2: Add env loading + factory call**

In `main.py`, near the existing voice setup (before `VoiceInterface(...)`):
```python
    wake_phrase = os.getenv("WAKE_PHRASE", "computer")
    wake_backend_name = os.getenv("WAKE_BACKEND", "openwakeword")
    wake_word_model = os.getenv("WAKE_WORD_MODEL", "hey_jarvis")
    wake_word_threshold = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))
    whisper_wake_model = os.getenv("WAKE_MODEL", "tiny")

    wake_backend, wake_msg = voice_backends.build_wake_backend(
        backend_name=wake_backend_name,
        model_name=wake_word_model,
        threshold=wake_word_threshold,
        wake_phrase=wake_phrase,
        whisper_model=whisper_wake_model,
    )
    print(wake_msg)
```

Update the `VoiceInterface(...)` call to pass `wake_backend=wake_backend`.

- [ ] **Step 3: Update .env.example**

Append to `.env.example`:
```
# Wake backend (Wave 1)
WAKE_BACKEND=openwakeword          # openwakeword | whisper (fallback)
WAKE_WORD_MODEL=hey_jarvis         # openWakeWord prebuilt model name
WAKE_WORD_THRESHOLD=0.5            # 0.0–1.0 activation confidence

# Legacy (consulted only when WAKE_BACKEND=whisper)
# WAKE_PHRASE=computer
# WAKE_MODEL=tiny
```

- [ ] **Step 4: Run full fast suite**

Run: `./scripts/test.sh`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add main.py .env.example
git commit -m "feat(voice): wire WAKE_BACKEND env var, default to openwakeword"
```

### Task 1.7: Wave 1 Pi smoke test (manual)

- [ ] **Step 1: Deploy to Pi**

On Pi: `git pull && source .venv/bin/activate && pip install -r requirements.txt && ./run.sh --voice`

- [ ] **Step 2: Validation checklist**

Run for ≥30 minutes ambient + 10 intentional wakes. Record:
- Number of false fires (target: 0)
- Number of intentional misses (target: ≤1 of 10)
- Any "thank you for watching" / "pewder" hallucinations (target: none reach the wake handler)

- [ ] **Step 3: If validation fails — set rollback**

In `.env` on Pi: `WAKE_BACKEND=whisper`. Restart. Should match pre-Wave-1 behaviour. File a follow-up issue rather than reverting the merge.

- [ ] **Step 4: If validation passes — proceed to Wave 2**

---

## Wave 2 — Silero VAD (replaces RMS endpointing)

### Task 2.1: Add `silero-vad` to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:
```
# Voice activity detection
silero-vad>=5.0
```

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install silero-vad`
Expected: pulls `onnxruntime` and the Silero VAD package.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat(deps): add silero-vad for endpointing"
```

### Task 2.2: Define `VadBackend` Protocol + `RmsVadBackend` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class RmsVadBackendTests(unittest.TestCase):
    def test_is_speech_true_when_amplitude_above_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        loud = (np.ones(1024, dtype=np.int16) * 5000).astype(np.int16)
        self.assertTrue(backend.is_speech(loud))

    def test_is_speech_false_when_amplitude_below_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        quiet = np.zeros(1024, dtype=np.int16)
        self.assertFalse(backend.is_speech(quiet))

    def test_reset_is_noop(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        backend.reset()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::RmsVadBackendTests -v`
Expected: 3 FAILs with `AttributeError: module 'core.voice_backends' has no attribute 'RmsVadBackend'`.

- [ ] **Step 3: Implement Protocol + class**

In `core/voice_backends.py`, after the wake backends, add:
```python
class VadBackend(Protocol):
    """Voice activity detector. Consumes audio chunks, returns speech bool."""
    def is_speech(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class RmsVadBackend:
    """Fallback VAD — amplitude threshold. Preserves current behaviour."""

    def __init__(self, threshold: int = 1000):
        self.threshold = threshold

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.int16:
            audio_chunk = audio_chunk.astype(np.int16)
        level = np.abs(audio_chunk).mean()
        return level > self.threshold

    def reset(self) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::RmsVadBackendTests -v`
Expected: 3 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add VadBackend protocol + RmsVadBackend fallback"
```

### Task 2.3: Implement `SileroVadBackend` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

**API note (silero-vad 6.x via `load_silero_vad()`):** the returned object is a TorchScript `RecursiveScriptModule` and is a **stateful streaming VAD** — `model(tensor, 16000)` rejects any tensor whose length is not exactly 512 samples at 16kHz (256 / 1024 / 1280 all raise). PyAudio gives us 1024-sample chunks, so the backend must keep an internal carry-over buffer that yields 512-sample frames to the model.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class SileroVadBackendTests(unittest.TestCase):
    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_true_when_score_above_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.85)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x  # passthrough

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # 1024 samples → exactly two 512-sample sub-frames consumed
        audio = np.zeros(1024, dtype=np.float32)

        self.assertTrue(backend.is_speech(audio))
        self.assertEqual(mock_model.call_count, 2)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_false_when_score_below_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.2)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        self.assertFalse(backend.is_speech(np.zeros(1024, dtype=np.float32)))

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_carry_over_short_chunks(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.0)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # Two 300-sample chunks: first call has no full 512-frame, second does
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 1)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_reset_clears_buffer_and_model_state(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        backend.is_speech(np.zeros(300, dtype=np.float32))  # leaves 300 samples in buffer
        backend.reset()
        # After reset, a 200-sample chunk should not yet trigger the model (buffer cleared)
        backend.is_speech(np.zeros(200, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        mock_model.reset_states.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::SileroVadBackendTests -v`
Expected: 4 FAILs with `AttributeError: module 'core.voice_backends' has no attribute 'SileroVadBackend'`.

- [ ] **Step 3: Implement the class**

In `core/voice_backends.py`, after `RmsVadBackend`, add:
```python
try:
    import silero_vad
    import torch
    _SILERO_AVAILABLE = True
except ImportError:
    silero_vad = None  # type: ignore[assignment]
    torch = None  # type: ignore[assignment]
    _SILERO_AVAILABLE = False


class SileroVadBackend:
    """Primary VAD — Silero TorchScript speech-probability model.

    Silero VAD only accepts 512-sample frames at 16kHz. PyAudio chunks are
    typically 1024 samples, so we keep a carry-over buffer that yields exact
    512-sample frames to the model. Returns True if any frame in the current
    call's accumulated audio scored above the threshold (conservative — a
    single speech-positive frame keeps `recording` armed in the endpoint loop).
    """

    FRAME_SIZE = 512

    def __init__(self, threshold: float = 0.5):
        if not _SILERO_AVAILABLE:
            raise ImportError("silero-vad not installed")
        logger.info("Loading Silero VAD model")
        self.threshold = threshold
        self.model = silero_vad.load_silero_vad()
        self._buffer = np.zeros(0, dtype=np.float32)

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32) / 32768.0

        self._buffer = np.concatenate([self._buffer, audio_chunk])

        any_speech = False
        while len(self._buffer) >= self.FRAME_SIZE:
            frame = self._buffer[: self.FRAME_SIZE]
            self._buffer = self._buffer[self.FRAME_SIZE :]
            tensor = torch.from_numpy(frame)
            score = self.model(tensor, 16000).item()
            if score >= self.threshold:
                any_speech = True
        return any_speech

    def reset(self) -> None:
        # Clear streaming carry-over and the model's internal LSTM state.
        # The endpoint loop calls reset() between sessions so a stale tail
        # doesn't carry into the next utterance.
        self._buffer = np.zeros(0, dtype=np.float32)
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::SileroVadBackendTests -v`
Expected: 4 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add SileroVadBackend with 512-frame carry-over buffer"
```

### Task 2.4: Add `build_vad_backend` factory (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class BuildVadBackendTests(unittest.TestCase):
    @patch("core.voice_backends.SileroVadBackend")
    def test_returns_silero_when_backend_is_silero(self, mock_silero):
        instance = object()
        mock_silero.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="silero", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("silero", message)

    @patch("core.voice_backends.RmsVadBackend")
    def test_returns_rms_when_backend_is_rms(self, mock_rms):
        instance = object()
        mock_rms.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="rms", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("rms", message)

    @patch("core.voice_backends.RmsVadBackend")
    @patch(
        "core.voice_backends.SileroVadBackend",
        side_effect=ImportError("silero-vad not installed"),
    )
    def test_falls_back_to_rms_on_silero_failure(self, mock_silero, mock_rms):
        instance = object()
        mock_rms.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="silero", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("fallback", message.lower())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::BuildVadBackendTests -v`
Expected: 3 FAILs.

- [ ] **Step 3: Implement the factory**

In `core/voice_backends.py`, after `SileroVadBackend`, add:
```python
def build_vad_backend(
    backend_name: str,
    threshold: float,
    rms_threshold: int,
) -> tuple[VadBackend, str]:
    """Select VAD backend by name with automatic fallback to RMS."""
    if backend_name == "silero":
        try:
            backend = SileroVadBackend(threshold=threshold)
            return backend, f"VAD backend: silero (threshold={threshold})"
        except (ImportError, Exception) as exc:
            logger.warning("Silero VAD unavailable (%s) — falling back to RMS", exc)
            backend = RmsVadBackend(threshold=rms_threshold)
            return backend, f"VAD backend: rms (threshold={rms_threshold}) — silero fallback"

    backend = RmsVadBackend(threshold=rms_threshold)
    return backend, f"VAD backend: rms (threshold={rms_threshold})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::BuildVadBackendTests -v`
Expected: 3 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add build_vad_backend factory with rms fallback"
```

### Task 2.5: Wire `_record_until_silence` to consume `VadBackend`

**Files:**
- Modify: `core/voice.py`
- Modify: `tests/test_main_voice_backend_selection.py` (or create new test)

- [ ] **Step 1: Update VoiceInterface constructor**

In `core/voice.py`, add `vad_backend` parameter and `vad_min_silence_ms`:
```python
    def __init__(
        self,
        ...
        wake_backend=None,
        vad_backend=None,
        vad_min_silence_ms: int = 700,
        ...
    ):
        ...
        self.vad_backend = vad_backend
        self.vad_min_silence_ms = vad_min_silence_ms
```

- [ ] **Step 2: Refactor _record_until_silence**

Replace the inline RMS check inside the recording loop. Inside `_record_until_silence`:
```python
        frames = []
        silence_ms = 0
        chunk_ms = int(self.CHUNK / self.RATE * 1000)
        max_wait_chunks = int(self.RATE / self.CHUNK * max_wait_seconds) if max_wait_seconds else 0
        waited_chunks = 0
        recording = False

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                frames.append(data)
                audio_chunk = np.frombuffer(data, dtype=np.int16)

                if self.vad_backend is not None:
                    is_speech = self.vad_backend.is_speech(audio_chunk)
                else:
                    level = np.abs(audio_chunk).mean()
                    is_speech = level > self.silence_threshold

                if is_speech:
                    recording = True
                    silence_ms = 0
                elif recording:
                    silence_ms += chunk_ms

                # Endpoint condition
                if self.vad_backend is not None:
                    endpoint = recording and silence_ms >= self.vad_min_silence_ms
                else:
                    silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
                    silence_frames = silence_ms // chunk_ms if chunk_ms else 0
                    endpoint = recording and silence_frames > silence_limit

                if endpoint:
                    if on_speech_done is not None:
                        try:
                            on_speech_done()
                        except Exception:
                            logger.warning("on_speech_done callback raised", exc_info=True)
                    break
                # ... rest of loop body (idle timeout) unchanged
```

- [ ] **Step 3: Add an integration test**

Append to `tests/test_main_voice_backend_selection.py`:
```python
class VadBackendIntegrationTests(unittest.TestCase):
    @patch("core.voice.pyaudio.PyAudio")
    @patch("core.voice.tempfile.NamedTemporaryFile")
    @patch("core.voice.wave.open")
    def test_record_until_silence_uses_vad_backend(
        self, mock_wave, mock_tmp, mock_pa
    ):
        from core.voice import VoiceInterface

        # 5 chunks of "speech" then 6 chunks of "silence" (each chunk ~64ms).
        speech_pattern = [True] * 5 + [False] * 6
        vad_backend = MagicMock()
        vad_backend.is_speech.side_effect = speech_pattern

        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\x00" * 2048
        mock_pa.return_value.open.return_value = mock_stream

        mock_tmp.return_value.__enter__.return_value.name = "/tmp/test.wav"

        voice = VoiceInterface(
            stt_backend=MagicMock(),
            tts_backend=MagicMock(),
            vad_backend=vad_backend,
            vad_min_silence_ms=200,
            enable_tts=False,
        )

        voice._record_until_silence()

        self.assertGreaterEqual(vad_backend.is_speech.call_count, 6)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/voice.py tests/test_main_voice_backend_selection.py
git commit -m "feat(voice): _record_until_silence delegates to VadBackend"
```

### Task 2.6: Wire env vars in `main.py`

**Files:**
- Modify: `main.py`
- Modify: `.env.example`

- [ ] **Step 1: Add env loading**

In `main.py` near the wake-backend section:
```python
    vad_backend_name = os.getenv("VAD_BACKEND", "silero")
    vad_threshold = float(os.getenv("VAD_THRESHOLD", "0.5"))
    vad_min_silence_ms = int(os.getenv("VAD_MIN_SILENCE_MS", "700"))
    rms_threshold = int(os.getenv("SILENCE_THRESHOLD", "1000"))

    vad_backend, vad_msg = voice_backends.build_vad_backend(
        backend_name=vad_backend_name,
        threshold=vad_threshold,
        rms_threshold=rms_threshold,
    )
    print(vad_msg)
```

Pass to `VoiceInterface(...)`:
```python
        vad_backend=vad_backend,
        vad_min_silence_ms=vad_min_silence_ms,
```

- [ ] **Step 2: Update .env.example**

Append to `.env.example`:
```
# VAD backend (Wave 2)
VAD_BACKEND=silero                 # silero | rms (fallback)
VAD_THRESHOLD=0.5                  # silero speech probability cutoff
VAD_MIN_SILENCE_MS=700             # ms of silence before endpointing

# Legacy (consulted only when VAD_BACKEND=rms)
# SILENCE_THRESHOLD=1000
# SILENCE_DURATION=2.0
```

- [ ] **Step 3: Run full fast suite**

Run: `./scripts/test.sh`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add main.py .env.example
git commit -m "feat(voice): wire VAD_BACKEND env var, default to silero"
```

### Task 2.7: Wave 2 Pi smoke test (manual)

- [ ] **Step 1: Deploy and validate**

On Pi: `git pull && pip install -r requirements.txt && ./run.sh --voice`. Run 10 short utterances + 5 long utterances. Confirm:
- No mid-sentence cutoffs.
- No empty transcripts.
- Endpoint feels snappier (~0.7s vs current 2s).

- [ ] **Step 2: If validation fails**

Set `VAD_BACKEND=rms` in `.env`; restart. Should match pre-Wave-2 behaviour.

---

## Wave 3 — `faster-whisper` + Whisper-small (CPU path)

### Task 3.1: Add `faster-whisper` to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:
```
# Faster CPU Whisper
faster-whisper>=1.0
```

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install faster-whisper`
Expected: pulls `ctranslate2`.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat(deps): add faster-whisper for CPU STT"
```

### Task 3.2: Implement `FasterWhisperBackend` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class FasterWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperModel")
    def test_transcribe_file_returns_concatenated_text(self, mock_model_cls):
        seg1 = MagicMock(text="Hello world.")
        seg2 = MagicMock(text=" Goodbye.")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        mock_model_cls.return_value = mock_model

        backend = voice_backends.FasterWhisperBackend(model_name="small")
        result = backend.transcribe_file("/tmp/fake.wav")

        self.assertEqual(result, "Hello world. Goodbye.")

    @patch("core.voice_backends.WhisperModel")
    def test_loads_model_with_int8_compute_type_for_cpu(self, mock_model_cls):
        voice_backends.FasterWhisperBackend(model_name="small")
        args, kwargs = mock_model_cls.call_args
        self.assertEqual(args[0], "small")
        self.assertEqual(kwargs.get("compute_type"), "int8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::FasterWhisperBackendTests -v`
Expected: 2 FAILs.

- [ ] **Step 3: Implement the class**

In `core/voice_backends.py`, after `HybridWhisperBackend`, add:
```python
try:
    from faster_whisper import WhisperModel
    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None  # type: ignore[assignment]
    _FASTER_WHISPER_AVAILABLE = False


class FasterWhisperBackend:
    """CPU STT backend using faster-whisper (CTranslate2). Drop-in for transcribe_file."""

    def __init__(self, model_name: str = "small"):
        if not _FASTER_WHISPER_AVAILABLE:
            raise ImportError("faster-whisper not installed")
        logger.info("Loading faster-whisper model: %s", model_name)
        self.model_name = model_name
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe_file(self, audio_file: str) -> str:
        segments, _info = self.model.transcribe(audio_file, language="en")
        return "".join(seg.text for seg in segments).strip()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::FasterWhisperBackendTests -v`
Expected: 2 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add FasterWhisperBackend (CPU STT)"
```

### Task 3.3: Split CPU/Hailo Whisper model selection (test-first)

**Files:**
- Modify: `core/voice_backends.py` (update `build_stt_backend` signature + body)
- Modify: `tests/test_voice_backends.py` (update existing tests + add new)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_backends.py`:
```python
class BuildSttBackendCpuPathTests(unittest.TestCase):
    @patch("core.voice_backends.FasterWhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_uses_faster_whisper_with_cpu_model_when_no_hailo(
        self, mock_runtime, mock_fw
    ):
        instance = object()
        mock_fw.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            wake_model="tiny",
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, instance)
        mock_fw.assert_called_once_with(model_name="small")
        self.assertIn("cpu:small", message)

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.FasterWhisperBackend",
        side_effect=ImportError("faster-whisper missing"),
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_openai_whisper_when_faster_whisper_missing(
        self, mock_runtime, mock_fw, mock_whisper
    ):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            wake_model="tiny",
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, instance)
        self.assertIn("openai-whisper fallback", message.lower())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::BuildSttBackendCpuPathTests -v`
Expected: 2 FAILs (signature mismatch).

- [ ] **Step 3: Update build_stt_backend signature and body**

Modify `core/voice_backends.py`. Change `build_stt_backend(wake_model, transcription_model)` to:
```python
def build_stt_backend(
    wake_model: str,
    transcription_model_cpu: str,
    transcription_model_hailo: str,
) -> tuple[SttBackend, str]:
    """Select STT backend. Hailo path uses transcription_model_hailo (must be in
    SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS); CPU path uses transcription_model_cpu
    via faster-whisper, falling back to openai-whisper on import failure."""

    def _build_cpu() -> tuple[SttBackend, str]:
        try:
            return (
                FasterWhisperBackend(model_name=transcription_model_cpu),
                f"cpu:{transcription_model_cpu} (faster-whisper)",
            )
        except (ImportError, Exception) as exc:
            logger.warning("faster-whisper unavailable (%s) — using openai-whisper", exc)
            return (
                WhisperBackend(
                    wake_model=wake_model,
                    transcription_model=transcription_model_cpu,
                ),
                f"cpu:{transcription_model_cpu} (openai-whisper fallback)",
            )

    if not hailo_runtime_available():
        cpu_backend, cpu_label = _build_cpu()
        return (
            cpu_backend,
            f"STT backend: {cpu_label} — Hailo runtime unavailable",
        )

    use_hailo_transcription = False
    fallback_reasons: list[str] = []

    if transcription_model_hailo in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        ok, reason = hailo_transcription_assets_available(transcription_model_hailo)
        if ok:
            try:
                hailo_transcription_self_check(transcription_model_hailo)
                use_hailo_transcription = True
            except Exception as exc:
                fallback_reasons.append(f"hailo transcription {exc}")
        else:
            fallback_reasons.append(reason)
    else:
        fallback_reasons.append("hailo transcription model variant unsupported")

    if not use_hailo_transcription:
        cpu_backend, cpu_label = _build_cpu()
        reason = fallback_reasons[0] if fallback_reasons else "Hailo unavailable"
        return (
            cpu_backend,
            f"STT backend: {cpu_label} — {reason}",
        )

    backend = HybridWhisperBackend(
        wake_model=wake_model,
        transcription_model=transcription_model_hailo,
        use_hailo_wake=False,
        use_hailo_transcription=True,
    )
    return (
        backend,
        f"STT backend: Hybrid Whisper (transcription=hailo:{transcription_model_hailo})",
    )
```

- [ ] **Step 4: Update existing build_stt_backend tests**

In `tests/test_voice_backends.py`, find existing `BuildSttBackendTests` class. Update each test that calls `voice_backends.build_stt_backend("tiny", "base")` to the new signature: `voice_backends.build_stt_backend(wake_model="tiny", transcription_model_cpu="base", transcription_model_hailo="base")`.

For tests that asserted exact message strings, update assertions to match new format (e.g. `"cpu:base"` instead of `"transcription=cpu:base"`).

- [ ] **Step 5: Run all voice_backends tests**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): split CPU/Hailo Whisper model in build_stt_backend"
```

### Task 3.4: Wire env vars in `main.py`

**Files:**
- Modify: `main.py`
- Modify: `.env.example`

- [ ] **Step 1: Update env loading**

In `main.py`, replace the existing `WHISPER_MODEL` lookup with:
```python
    legacy_whisper = os.getenv("WHISPER_MODEL", "base")
    transcription_model_cpu = os.getenv("WHISPER_MODEL_CPU", legacy_whisper if legacy_whisper != "base" else "small")
    transcription_model_hailo = os.getenv("WHISPER_MODEL_HAILO", legacy_whisper)
```

Note: legacy `WHISPER_MODEL` honoured only when explicitly set to a non-default value, otherwise CPU defaults to `small`.

Update the `build_stt_backend(...)` call site:
```python
    stt_backend, stt_msg = voice_backends.build_stt_backend(
        wake_model=os.getenv("WAKE_MODEL", "tiny"),
        transcription_model_cpu=transcription_model_cpu,
        transcription_model_hailo=transcription_model_hailo,
    )
    print(stt_msg)
```

- [ ] **Step 2: Update .env.example**

Append:
```
# STT model selection (Wave 3)
WHISPER_MODEL_CPU=small             # faster-whisper model on CPU path
WHISPER_MODEL_HAILO=base            # Hailo HEF variant (only base/tiny published)

# Legacy (used as fallback if the two above are unset)
# WHISPER_MODEL=base
```

- [ ] **Step 3: Run full fast suite**

Run: `./scripts/test.sh`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add main.py .env.example
git commit -m "feat(voice): WHISPER_MODEL_CPU/HAILO split, default CPU to small"
```

### Task 3.5: Wave 3 Pi smoke test (manual)

- [ ] **Step 1: Deploy + validate**

On Pi (Hailo present): confirm Hailo path still selected (`STT backend: Hybrid Whisper (transcription=hailo:base)` in startup log).
On Pi without Hailo or with `WHISPER_MODEL_HAILO=small` (forces fallback): confirm faster-whisper-small selected.

Run 15 representative utterances. Subjective accuracy bar — should be ≥ today.

- [ ] **Step 2: If regression**

Set `WHISPER_MODEL_CPU=base` in `.env` to revert to base on the CPU path.

---

## Wave 4 — LLM → TTS streaming

### Task 4.1: Add `KokoroTTSBackend.speak_stream` (test-first)

**Files:**
- Modify: `core/voice_backends.py`
- Create: `tests/test_kokoro_stream.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kokoro_stream.py`:
```python
import unittest
from unittest.mock import MagicMock, patch


class SpeakStreamTests(unittest.TestCase):
    def _make_backend(self):
        from core import voice_backends
        with patch.object(voice_backends, "KPipeline"):
            backend = voice_backends.KokoroTTSBackend()
        backend.pipeline = MagicMock()
        backend.pipeline.return_value = iter([])  # empty audio chunks
        return backend

    @patch("core.voice_backends.sd")
    def test_flushes_on_period(self, mock_sd):
        backend = self._make_backend()
        chunks = iter(["Hello", " world", ".", " More"])
        backend.speak_stream(chunks)

        # Two flushes: "Hello world." and " More" (final flush).
        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_flushes_on_question_and_exclaim(self, mock_sd):
        backend = self._make_backend()
        chunks = iter(["Are you sure", "?", " Yes", "!"])
        backend.speak_stream(chunks)

        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_flushes_at_buffer_cap(self, mock_sd):
        backend = self._make_backend()
        # 250 chars without sentence boundary
        long = "a" * 250
        backend.speak_stream(iter([long]))

        # Cap is 200, then trailing flush of remaining 50.
        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_no_flush_on_empty_input(self, mock_sd):
        backend = self._make_backend()
        backend.speak_stream(iter([]))
        self.assertEqual(backend.pipeline.call_count, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kokoro_stream.py -v`
Expected: 4 FAILs (`speak_stream` not defined).

- [ ] **Step 3: Implement speak_stream**

In `core/voice_backends.py`, add to `KokoroTTSBackend`:
```python
    SENTENCE_TERMINATORS = (".", "?", "!", "\n")
    BUFFER_CAP = 200

    def speak_stream(self, chunks) -> None:
        """Consume an iterable of text deltas, flush sentences to TTS as they form."""
        buffer = ""

        with sd.OutputStream(
            samplerate=self.output_samplerate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        ) as stream:
            def _flush(text: str) -> None:
                if not text.strip():
                    return
                for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
                    stream.write(resample(audio, KOKORO_SAMPLE_RATE, self.output_samplerate))

            for delta in chunks:
                buffer += delta
                while True:
                    boundary = -1
                    for term in self.SENTENCE_TERMINATORS:
                        idx = buffer.find(term)
                        if idx != -1 and (boundary == -1 or idx < boundary):
                            boundary = idx
                    if boundary != -1:
                        flush_text = buffer[: boundary + 1]
                        buffer = buffer[boundary + 1 :]
                        _flush(flush_text)
                        continue
                    if len(buffer) >= self.BUFFER_CAP:
                        _flush(buffer[: self.BUFFER_CAP])
                        buffer = buffer[self.BUFFER_CAP :]
                        continue
                    break

            if buffer.strip():
                _flush(buffer)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_kokoro_stream.py -v`
Expected: 4 PASSes.

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_stream.py
git commit -m "feat(tts): KokoroTTSBackend.speak_stream with sentence-boundary flush"
```

### Task 4.2: Convert `OllamaToolLoop` to streaming (test-first)

**Files:**
- Modify: `core/ollama_tool_loop.py`
- Modify: `tests/test_ollama_tool_loop.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ollama_tool_loop.py`:
```python
class OllamaToolLoopStreamingTests(unittest.TestCase):
    @patch("core.ollama_tool_loop.requests.post")
    def test_streaming_invokes_on_chunk_for_each_delta(self, mock_post):
        from core.ollama_tool_loop import OllamaToolLoop

        # Simulated NDJSON stream from Ollama with stream=True.
        lines = [
            b'{"message":{"content":"Hello"},"done":false}',
            b'{"message":{"content":" world"},"done":false}',
            b'{"message":{"content":"."},"done":true}',
        ]
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value.__enter__.return_value = mock_resp
        mock_post.return_value.__exit__.return_value = False

        loop = OllamaToolLoop(...)  # construct with mocked deps as in existing tests
        chunks_seen = []
        result = loop.run_streaming(
            user_message="hi",
            system_prompt="you are a bot",
            on_chunk=chunks_seen.append,
        )

        self.assertEqual(chunks_seen, ["Hello", " world", "."])
        self.assertEqual(result, "Hello world.")
```

(Use the existing test setup pattern in `OllamaToolLoopTests` for constructing the loop with mock dependencies.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ollama_tool_loop.py::OllamaToolLoopStreamingTests -v`
Expected: FAIL (`run_streaming` not defined).

- [ ] **Step 3: Implement run_streaming**

In `core/ollama_tool_loop.py`, add a method that mirrors the existing `run` but streams the final text response. Add after the existing `run` method:
```python
    def run_streaming(
        self,
        user_message: str,
        system_prompt: str,
        on_chunk: Callable[[str], None],
    ) -> str | EscalateSignal:
        """Streaming variant of run(). Yields text deltas via on_chunk; returns full text.

        Tool-call rounds are NOT streamed (they have no spoken text). Only the final
        text-only assistant turn is streamed.
        """
        # Run the tool loop as before but in non-streaming mode for tool rounds.
        # When the loop reaches a final text-only turn, re-issue with stream=True.
        result = self.run(user_message=user_message, system_prompt=system_prompt)
        if isinstance(result, EscalateSignal):
            return result

        # If we already have a final response and it's text, re-issue the last
        # request with stream=True to stream tokens.
        # NB: simplest correct implementation — re-call Ollama with stream=True
        # over the conversation state and stream that. Tool rounds have already
        # committed.
        payload = {
            "model": self.model,
            "messages": self._build_messages(system_prompt),
            "stream": True,
            "options": {"num_ctx": self.context_window},
        }
        full_text = ""
        with requests.post(
            self.endpoint,
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                delta = data.get("message", {}).get("content", "")
                if delta:
                    full_text += delta
                    on_chunk(delta)
                if data.get("done"):
                    break
        return full_text
```

(Adapt to the actual constructor / method names and helpers in `core/ollama_tool_loop.py`. The key change: a new method that streams, leaves the existing non-streaming `run` untouched.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_ollama_tool_loop.py -v`
Expected: all PASS, including new streaming test.

- [ ] **Step 5: Commit**

```bash
git add core/ollama_tool_loop.py tests/test_ollama_tool_loop.py
git commit -m "feat(ollama): add run_streaming with on_chunk callback"
```

### Task 4.3: Add `Orchestrator.process_message_stream` (test-first)

**Files:**
- Modify: `core/orchestrator.py`
- Create: `tests/test_orchestrator_streaming.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_streaming.py`:
```python
import unittest
from unittest.mock import MagicMock, patch


class ProcessMessageStreamTests(unittest.TestCase):
    @patch("core.orchestrator.Anthropic")
    def test_returns_full_text_and_calls_on_chunk_per_delta(self, mock_anthropic):
        from core.orchestrator import Orchestrator

        # Set up mocked Claude streaming response.
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__.return_value.text_stream = iter(
            ["Hello", " there", "."]
        )
        mock_stream_ctx.__enter__.return_value.get_final_message.return_value = (
            MagicMock(content=[MagicMock(type="text", text="Hello there.")])
        )
        mock_anthropic.return_value.messages.stream.return_value = mock_stream_ctx

        orchestrator = Orchestrator(...)  # use the existing construction pattern
        chunks = []
        result = orchestrator.process_message_stream("hi", on_chunk=chunks.append)

        self.assertEqual(chunks, ["Hello", " there", "."])
        self.assertEqual(result, "Hello there.")

    @patch("core.orchestrator.Anthropic")
    def test_falls_back_to_non_streaming_when_tool_calls_present(
        self, mock_anthropic
    ):
        # Tool-use response: stream nothing, return full text after tools complete.
        # See existing tool-loop test patterns.
        ...
```

(Use the existing orchestrator construction pattern from other tests in `tests/test_orchestrator_*.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_streaming.py -v`
Expected: FAIL (`process_message_stream` not defined).

- [ ] **Step 3: Implement process_message_stream**

In `core/orchestrator.py`, add a new public method that mirrors `process_message` but uses Anthropic's streaming API for the final text turn (and `OllamaToolLoop.run_streaming` when on the Ollama tier):
```python
    def process_message_stream(
        self,
        user_message: str,
        on_chunk: Callable[[str], None],
    ) -> str:
        """Streaming variant of process_message. Yields deltas via on_chunk;
        returns full accumulated text. Tool rounds are non-streaming; only the
        final text response streams."""
        with self.lock:
            return self._process_message_stream(user_message, on_chunk)

    def _process_message_stream(
        self,
        user_message: str,
        on_chunk: Callable[[str], None],
    ) -> str:
        # Reuse routing logic from _process_message — tier router, conversation
        # state, archive, etc. Diverge only at the final response generation:
        # - Ollama tier: call self.ollama_loop.run_streaming(..., on_chunk=on_chunk)
        # - Claude tier: use self.client.messages.stream(...) and forward
        #   text_stream deltas to on_chunk; commit final assembled text to state.
        # - Tool-use intermediate rounds: do not stream; commit and proceed.
        ...
```

(Implementation must mirror the routing/commit/state logic in the existing `_process_message` exactly; the ONLY change is which API call produces the final text. Read `core/orchestrator.py` lines 281–406 carefully and copy the structure.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_streaming.py tests/test_orchestrator_archive.py tests/test_orchestrator_checkpoint.py -v`
Expected: all PASS, no regressions to existing orchestrator tests.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py tests/test_orchestrator_streaming.py
git commit -m "feat(orch): add process_message_stream with on_chunk callback"
```

### Task 4.4: Wire voice loop in `main.py`

**Files:**
- Modify: `main.py`
- Modify: `core/voice.py` (add `speak_stream_feeder` helper if absent)

- [ ] **Step 1: Add a feeder helper to VoiceInterface**

In `core/voice.py`, add to `VoiceInterface`:
```python
    def speak_stream_feeder(self):
        """Return (push, finalize) pair for streaming text deltas to TTS."""
        import queue, threading

        q: queue.Queue = queue.Queue()
        SENTINEL = object()

        def _consume():
            def gen():
                while True:
                    item = q.get()
                    if item is SENTINEL:
                        return
                    yield item

            if self.enable_tts and self.tts_backend is not None:
                self.tts_backend.speak_stream(gen())

        thread = threading.Thread(target=_consume, daemon=True)
        thread.start()

        def push(delta: str) -> None:
            q.put(delta)

        def finalize() -> None:
            q.put(SENTINEL)
            thread.join(timeout=10)

        return push, finalize
```

- [ ] **Step 2: Update voice loop in main.py**

In `main.py`, replace the `response = orchestrator.process_message(transcription); voice.speak(response)` block with:
```python
                        if os.getenv("LLM_STREAM_TO_TTS", "true").lower() == "true":
                            push, finalize = voice.speak_stream_feeder()
                            try:
                                response = orchestrator.process_message_stream(
                                    transcription, on_chunk=push
                                )
                            finally:
                                finalize()
                        else:
                            response = orchestrator.process_message(transcription)
                            voice.speak(response)
```

- [ ] **Step 3: Run full fast suite**

Run: `./scripts/test.sh`
Expected: green.

- [ ] **Step 4: Update .env.example**

Append:
```
# Streaming (Wave 4)
LLM_STREAM_TO_TTS=true              # master switch for LLM→TTS streaming
TTS_STREAM_SENTENCE_FLUSH=true      # split on sentence boundaries
```

- [ ] **Step 5: Commit**

```bash
git add main.py core/voice.py .env.example
git commit -m "feat(voice): wire streaming voice loop with LLM_STREAM_TO_TTS switch"
```

### Task 4.5: Wave 4 Pi smoke test (manual)

- [ ] **Step 1: Deploy and time end-to-end**

On Pi: 5 short queries, 5 long queries. Stopwatch from "stop talking" to "first audio out." Target ≤ 3s on a typical short query.

- [ ] **Step 2: If regression / mid-utterance audio glitches**

Set `LLM_STREAM_TO_TTS=false` in `.env`. Restart. Should match pre-Wave-4 buffered behaviour.

- [ ] **Step 3: Update WORKING_MEMORY.md**

Add a 2026-05-XX milestone bullet under `Recent Durable Milestones` summarising the four-wave pipeline ship and the measured "stop-talking → first-audio" delta.

- [ ] **Step 4: Final commit**

```bash
git add WORKING_MEMORY.md
git commit -m "docs(memory): record voice-pipeline ship + measured latency delta"
```

---

## Self-review notes (filled in by writer, not the executor)

- All four spec swaps have a Wave with a closing Pi smoke test and a documented rollback flag.
- All factory functions test the fallback path explicitly.
- No "TBD"; one `...` placeholder in Task 4.3 Step 3 deliberately delegates to "read the existing _process_message and copy structure" — this is concrete work, not a missing requirement, because the orchestrator file is too large to inline a duplicate.
- The `WHISPER_MODEL_CPU` / `WHISPER_MODEL_HAILO` split required updating existing `BuildSttBackendTests` (called out explicitly in Task 3.3 Step 4).
- `LLM_STREAM_TO_TTS` and `TTS_STREAM_SENTENCE_FLUSH` env keys appear in the spec; the latter is documented in `.env.example` but not yet branched on in code (`KokoroTTSBackend.speak_stream` always splits). Acceptable as the kill switch is `LLM_STREAM_TO_TTS`; if sentence-flush misbehaves it can be added in a follow-up rather than this initial ship.
