# Voice pipeline: faster wake, better STT, streaming TTS

**Date:** 2026-05-04
**Status:** Design approved, ready for implementation plan
**Owner:** Mason (M8SON)

## Problem

Two roughly equal pain points in the current MiniClaw voice loop on Pi 5:

1. **Accuracy.** Whisper-tiny wake-stream hallucinates phrases (`thank you for watching`, `pewder`/`vacutor` for "computer"). RMS-amplitude endpointing truncates utterances mid-word, feeding garbage into Whisper-base. Net effect: MiniClaw mishears or false-fires often enough to be a daily friction.
2. **Speed.** After STT completes, the user waits 7–10s in silence while Ollama generates a full response on Pi 5 CPU (~3–5 tok/s) before TTS begins. The wait dominates perceived latency.

## Goals

- Eliminate wake-stream hallucinations.
- Stop truncating utterances at silence-threshold edges.
- Cut "stop talking → start hearing reply" from ~8–12s to ~2–3s on typical short replies.
- Keep MiniClaw's current architecture (single Pi 5, USB ReSpeaker XVF3800, AI HAT+ Hailo for full transcription, no satellite/ESPHome rearchitecture).
- Each change ships independently with a working fallback to today's behaviour, controlled by `.env`.

## Non-goals

- Satellite / ESPHome / OVOS rearchitecture.
- STT-partial streaming (token output during user speech).
- Hailo recompile of Whisper-small (HEF only published for `base`; Hailo path stays at `base`).
- Running openWakeWord on the Hailo NPU (CPU is plenty; Hailo stays focused on full transcription).
- TTS interruption / barge-in (already deferred per CLAUDE.md roadmap).
- Mic-gain investigation on the XVF3800 (flagged as follow-up, not in this work).

## Approach

Four sequential, independently shippable swaps. Each lands behind a `.env` flag with a fallback to today's behaviour.

### 1. Wake — `openWakeWord` replaces Whisper-tiny

`openWakeWord` is a purpose-built keyword spotter (TFLite, ~1MB models). Whisper-tiny was never designed to be a wake detector; using it for that job is the root cause of the hallucinations. Default wake word: `hey_jarvis` (from openWakeWord's prebuilt model set). Custom-trained `miniclaw` / `computer` models are a future option, not in this work.

CPU only. Runs at ~3% of one Pi 5 core, real-time. Hailo NPU stays focused on full-utterance transcription.

### 2. Endpointing — Silero VAD replaces RMS threshold

Silero VAD (ONNX, ~10MB) replaces the amplitude-counter loop in `_record_until_silence`. Eliminates mid-sentence cutoffs and empty transcripts caused by amplitude dipping below `SILENCE_THRESHOLD` between words.

### 3. Post-wake STT — `faster-whisper` with `small` model

`faster-whisper` is a CTranslate2 reimplementation of OpenAI Whisper. Same model weights, ~30% faster on CPU. Bumps the CPU-path model from `base` to `small` for noticeably better word accuracy, paid for by the CT2 speedup. Hailo path is untouched and stays at `base` (Hailo HEF only published for `base`).

### 4. Perceived latency — stream Ollama tokens to Kokoro

Flip `OllamaToolLoop`'s `"stream": False` to `True`, parse line-delimited JSON chunks, accumulate the response while flushing on sentence boundaries. Each completed sentence (`.` / `?` / `!` / 200-char buffer cap) is fed to a new `KokoroTTSBackend.speak_stream()` method that pushes audio through the existing `sd.OutputStream` as it arrives.

User starts hearing the response within ~1–2s of STT completing instead of waiting 7–10s for the full response.

The Claude path uses the Anthropic SDK's `messages.stream()`. Tool-use turns only stream the *final* text response, not intermediate tool-call rounds.

## Architecture changes

Three abstractions in `core/voice_backends.py` evolve. The wake job and the transcribe job are bundled today because both run Whisper. Once wake becomes a different *kind* of model, that bundle comes apart cleanly.

### Protocol split

**Today:**
```python
class SttBackend(Protocol):
    def transcribe_wake_audio(self, audio_float) -> str: ...
    def transcribe_file(self, audio_file: str) -> str: ...
```

**After:**
```python
class WakeBackend(Protocol):
    def detect(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...

class VadBackend(Protocol):
    def is_speech(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...

class SttBackend(Protocol):
    def transcribe_file(self, audio_file: str) -> str: ...
```

### Implementations (new + retained)

| Protocol | New default | Fallback (kept for `.env` rollback) |
|---|---|---|
| `WakeBackend` | `OpenWakeWordBackend` | `WhisperWakeBackend` (wraps existing Whisper-tiny path) |
| `VadBackend` | `SileroVadBackend` | `RmsVadBackend` (current threshold logic) |
| `SttBackend` (CPU) | `FasterWhisperBackend(model="small")` | `WhisperBackend(model="base")` (existing openai-whisper) |
| `SttBackend` (Hailo) | `HybridWhisperBackend` (unchanged) | — |

Factory functions `build_wake_backend()`, `build_vad_backend()`, and the existing `build_stt_backend()` select implementation by `.env`, falling back automatically on import or self-check failure (same pattern as the existing Hailo→CPU fallback).

### Streaming changes

- `KokoroTTSBackend.speak_stream(chunk_iter: Iterator[str]) -> None` — accumulates text, flushes on sentence boundary or 200-char cap, pushes each flushed chunk through Kokoro into the existing `sd.OutputStream`. The current `speak(text)` stays unchanged for non-streaming callers (R2-D2 chirps, scheduled announcements).
- `OllamaToolLoop` — flip `"stream": False` to `True`, parse line-delimited JSON, expose deltas via callback. Final accumulated text still returned for archive/state commit.
- `Orchestrator.process_message_stream(user_message, on_chunk: Callable[[str], None]) -> str` — new method. Existing `process_message()` keeps working unchanged for text mode and tests. `main.py`'s voice loop calls the streaming variant.

### Voice loop integration

In `core/voice.py`:

- `wait_for_wake_word()` consumes a `WakeBackend` instead of inlining Whisper-tiny.
- `_record_until_silence()` consumes a `VadBackend` instead of the inline RMS loop. The `silence_frames` counter becomes a `silence_ms` accumulator gated by `VAD_MIN_SILENCE_MS`.
- `speak_stream()` is added alongside existing `speak()`.

In `main.py`:

- Voice loop calls `orchestrator.process_message_stream(text, on_chunk=voice.speak_stream_feeder)` instead of `orchestrator.process_message(text)` followed by `voice.speak(response)`.
- Text mode is untouched.

## Configuration

### New `.env` keys

```
# Wake backend
WAKE_BACKEND=openwakeword              # openwakeword | whisper (fallback)
WAKE_WORD_MODEL=hey_jarvis             # openWakeWord prebuilt model name
WAKE_WORD_THRESHOLD=0.5                # 0.0–1.0 activation confidence

# VAD backend
VAD_BACKEND=silero                     # silero | rms (fallback)
VAD_THRESHOLD=0.5                      # silero speech probability cutoff
VAD_MIN_SILENCE_MS=700                 # ms of silence before endpointing

# Streaming
LLM_STREAM_TO_TTS=true                 # master switch for LLM→TTS streaming
TTS_STREAM_SENTENCE_FLUSH=true         # split on sentence boundaries
```

### Split: CPU vs Hailo Whisper model

Today, `WHISPER_MODEL` is a single value used for both Hailo and CPU paths. Bumping it to `small` globally would cause the Hailo path to silently fall back to CPU (Hailo HEF is only published for `{base, tiny, base.en, tiny.en}` — `small` isn't supported). We don't want to lose the NPU.

Split into two keys:

```
WHISPER_MODEL_CPU=small                # CPU faster-whisper model (new default; was base via WHISPER_MODEL)
WHISPER_MODEL_HAILO=base               # Hailo HEF variant (unchanged)
```

`build_stt_backend()` reads both. For backwards compatibility, if only the legacy `WHISPER_MODEL` is set (and the new keys are not), it's used as the value for both — preserving today's behaviour for anyone who hasn't migrated their `.env`.

### Preserved

`WAKE_PHRASE`, `WAKE_MODEL`, `SILENCE_THRESHOLD`, `SILENCE_DURATION`, `WHISPER_MODEL` (legacy) stay in `.env.example` and are consulted only when the corresponding fallback backend is selected (or, for `WHISPER_MODEL`, only when neither `WHISPER_MODEL_CPU` nor `WHISPER_MODEL_HAILO` is set). Setting `WAKE_BACKEND=whisper` and `VAD_BACKEND=rms` should yield identical behaviour to today.

## Data flow

### Today

```
mic → PyAudio (2s window, every 2s)
     → Whisper-tiny.transcribe()           [200–500ms per window, hallucinates]
     → substring match WAKE_PHRASE
     → keep stream open
     → loop: chunks → RMS check → silence_frames counter
     → temp WAV
     → Whisper-base.transcribe(file)       [Hailo or CPU; ~1–2s on CPU]
     → orchestrator.process_message(text)  [BLOCKS until full LLM response — 7–10s]
     → voice.speak(full_text)              [Kokoro streams audio per sentence]
```

### After

```
mic → PyAudio (continuous chunks ~80ms)
     → openWakeWord.detect(chunk)          [<5ms per chunk, no hallucination]
     → keep stream open
     → loop: chunks → Silero VAD.is_speech(chunk) → endpoint when silence_ms ≥ MIN
     → temp WAV
     → faster-whisper.transcribe(file)     [Hailo unchanged; CPU now `small` ~1.3x realtime]
     → orchestrator.process_message_stream(text, on_chunk=speak_stream_feeder)
                                           [Ollama streams JSON chunks]
                                           [text accumulates → flush on sentence boundary]
                                           [Kokoro starts speaking sentence 1 ~1–2s after STT]
     → speak_stream finalises; full text returned for archive/state commit
```

### Latency (rough, Pi 5)

| Stage | Today | After |
|---|---|---|
| Wake detect | 200–500ms / window, false fires | <5ms / chunk, no false fires |
| Endpoint after speech ends | 2.0s (`SILENCE_DURATION`) | ~0.7s (`VAD_MIN_SILENCE_MS`) |
| Post-wake STT (CPU path) | ~1–2s for `base` | ~1–1.5s for `small` via faster-whisper |
| Stop-talking → first audio | 8–12s | 2–3s |

## Error handling & fallbacks

| Failure | Behaviour |
|---|---|
| `openwakeword` package missing | `build_wake_backend()` logs warning, falls back to `WhisperWakeBackend`. |
| openWakeWord model file missing | Auto-download (library handles this); on persistent failure → Whisper-tiny wake fallback. |
| Silero ONNX missing | Auto-download via `silero-vad` package; on failure → `RmsVadBackend`. |
| `faster-whisper` not installed | Fall back to `WhisperBackend(model="base")`. Logged. |
| Ollama streaming returns malformed chunk | Treat as escalation to Claude (existing escalation path). |
| Kokoro errors mid-stream | Log, finalise `OutputStream` cleanly, skip remainder of utterance. Conversation continues. |
| `LLM_STREAM_TO_TTS=false` | Master kill switch — whole turn buffers as today, then `speak(full_text)`. |

**Principle:** every new component has a working fallback to today's behaviour, controlled by `.env`. If a swap regresses on real Pi hardware, recovery is a one-line `.env` edit.

## Edge cases

- **Tool-only LLM turn (no spoken text).** `on_chunk` never fires; after the tool loop completes, fall back to current `speak(full_text)`.
- **Runaway un-punctuated paragraph.** Sentence flush has a 200-char buffer cap so audio still emits, just chunked at the cap.
- **openWakeWord fails to load at startup.** Logged warning, automatic fallback to `WhisperWakeBackend`. Same pattern as the existing Hailo→CPU fallback.
- **VAD endpoints too eagerly on quiet speech.** `VAD_THRESHOLD` is the lever; documented in `.env.example`.

## Testing

### Unit / fast suite

- `OpenWakeWordBackend.detect()` — silence vs. recorded sample fixtures, threshold behaviour.
- `SileroVadBackend.is_speech()` — silence vs. speech fixtures.
- `FasterWhisperBackend.transcribe_file()` — short fixture WAV, assert transcript contains expected words. Skip-if-model-missing CI guard.
- `KokoroTTSBackend.speak_stream` — feed token-by-token, assert sentence-boundary flush, assert 200-char buffer cap. **No actual audio output in tests** (mock `sd.OutputStream`).
- `OllamaToolLoop` streaming path — mock `requests.post` to yield JSON-delimited chunks, assert callback receives expected text deltas.
- `Orchestrator.process_message_stream()` — mock streaming clients, assert chunk flow and final return value.
- Fallback wiring — assert `build_wake_backend()` falls back to `WhisperWakeBackend` when openWakeWord import fails.

### Manual Pi validation (per swap, before merging the next)

- **openWakeWord:** 30 mins ambient, count false fires; verify `thank you for watching` no longer wakes; verify intentional wake hits.
- **Silero VAD:** 10 short utterances + 5 long utterances, no mid-sentence cutoffs, no empty transcripts.
- **faster-whisper-small:** same 15 utterances, compare transcripts to today's `base` output. Subjective accuracy bar.
- **Streaming:** stopwatch from "stop talking" to "first audio out." Target ≤ 3s on a typical query.

CI runs the existing fast suite via `.github/workflows`. Pi validation is manual per MEMORY.md ("Dashboard end-to-end validation on real Pi hardware is still pending" — same operational reality applies here).

## Rollout order

1. **openWakeWord swap.** Smallest blast radius, biggest accuracy win. Independently mergeable.
2. **Silero VAD.** Replaces RMS path. Independently mergeable.
3. **faster-whisper + `small`.** Drop-in CPU-path swap. Independently mergeable.
4. **LLM → TTS streaming.** Largest perceived-latency win. Last because it's the most cross-cutting (orchestrator + ollama_tool_loop + voice + main).

Each step is a separate PR. Each PR includes its `.env` knob and fallback, so rollback is configuration-only.

## Open follow-ups (not in scope, flagged for after)

- XVF3800 mic-gain calibration (`arecord` levels; possible 2-channel beamform + mono downmix halving effective volume).
- Custom-trained openWakeWord model for `miniclaw` or `computer` if `hey_jarvis` becomes the long-term wake word.
- Future Hailo offload of openWakeWord *only if* Hailo NPU adds real headroom in some future config; currently not justified.
