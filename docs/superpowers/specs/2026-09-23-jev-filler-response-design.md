# Jev-classified filler responses (fill the STT→answer gap with speech, not a beep)

**Date:** 2026-09-23
**Status:** Draft — for review
**Owner:** Mason
**Related:**
- `docs/laya-feasibility-report.md` — Laya (local 421M-param classifier) rejected
  for this use case: Pi 5 CPU contention with Kokoro/Whisper, no fast ARM64 path.
- `docs/research/2026-09-23-filler-phrase-classifier-research.md` — external
  research on filler-phrase UX, ConvFill prior art, ElevenLabs TTFA numbers,
  and confidence-gated fallback guidance.
- `docs/superpowers/specs/2026-06-18-voice-naturalness-latency-design.md` —
  existing `on_speech_done` → `play_thinking_sound` cue this feature extends.
- `docs/superpowers/specs/2026-07-23-elevenlabs-tts-backend-design.md` —
  existing ElevenLabs Flash v2.5 backend this feature reuses for pre-synthesis.

## 1. Problem

Today, the moment the user stops talking, Kaizen plays a generic R2-D2 warble
(`play_thinking_sound`) and then waits — silently, from the user's
perspective — through STT wrap-up, `TierRouter` classification, and the full
Claude/tool-calling round trip (2–5s typically; the project's own profiling
docs record 13–16s for some tool-calling turns) before any words are spoken.

Real speech ("Let me check the weather for you...") in that gap would read as
far more natural and responsive than a beep, per both the project's own
latency-tuning history and external UX research (see linked research doc,
§1 and §3): topic-grounded fillers materially reduce *perceived* latency and
are well inside the safety margin of the real answer's latency, provided the
filler classification itself is fast and doesn't block or contend with the
existing pipeline.

## 2. Decision: Jev only, regex fallback deferred

Two classifier options were evaluated:

- **Laya** (local, ~421M-param transformer) — **rejected**. Its own
  documented CPU latency (193–464ms) is a best-case figure on unspecified,
  likely x86 hardware; on the Pi 5's Cortex-A76 (no AVX, no server memory
  bandwidth) it would plausibly run far slower, and worse, it would contend
  for the same 4 CPU cores Kokoro/faster-whisper are already saturating at
  the exact moment this feature fires. See `docs/laya-feasibility-report.md`
  for the full writeup.
- **Jev** (TypeSafe AI, hosted `Choice` primitive) — **selected**. It runs on
  TypeSafe's infrastructure, not the Pi's CPU, so it doesn't compete with
  Kokoro/Whisper for cycles. Third-party benchmarks put it at ~236–276ms p50
  end-to-end (network + inference); TypeSafe's own docs cite 70–500ms e2e.
  Either figure disappears into Kaizen's own 2–5s+ real-answer latency
  budget.

**This spec implements Jev only.** A regex/`TierRouter`-style fallback for
when Jev is slow, errors, or is unreachable is **explicitly designed for but
not implemented in v1** (see §7, Open Questions) — v1 degrades to *no filler
this turn* (silence, same as today) rather than a second code path, to keep
the initial change small and reviewable. Mason: confirm this phased approach
before implementation, or say "build the regex fallback now too" and it's a
small addition to the same design (§6.4 sketches it).

## 3. Goal / success criteria

- After the user finishes speaking and the transcript is available, a
  short, topic-relevant filler phrase plays via pre-cached ElevenLabs audio
  **while** the real Claude/tool-calling turn runs, with no added dead air
  versus today's behavior (the existing `on_speech_done` beep is replaced,
  not stacked — see §6.3).
- Jev classification has a hard timeout (target 500ms) so a slow or dead
  API can never make a turn slower than today's baseline.
- Misclassification degrades gracefully: below a confidence threshold, no
  specific filler plays (falls through to the existing generic
  `play_thinking_sound` beep) rather than confidently naming the wrong
  topic — per the confidence-gated pattern the research doc identifies as
  the standard mitigation (research doc §3).
- Filler audio is **pre-synthesized once and cached to disk**, not
  synthesized live per turn — this removes ElevenLabs network latency from
  the hot path entirely for the filler (only the *real* answer still goes
  through live ElevenLabs streaming, unchanged).
- No regression to the existing barge-in, ack-chime, or response-ready-cue
  behavior.

## 4. Filler phrase library — format and pre-synthesis

### 4.1 Why cache instead of calling ElevenLabs live for fillers

ElevenLabs has no "phrase library" primitive — every `text_to_speech.stream`
call synthesizes fresh audio over the network. For a small, fixed set of
filler lines (one or a few per category, rarely changed), synthesizing live
on every turn would add ElevenLabs' own network/TTFA latency (measured
150–500ms depending on region, per the research doc §2) on top of Jev's
call, for audio whose *text* never changes. Pre-synthesizing once and
caching the raw PCM to disk removes that entirely: filler playback becomes
a local file read + `sd.play`, matching the existing `play_ack_sound`
pattern (`core/voice.py:509-530`) almost exactly.

### 4.2 Library format — `config/filler_phrases.yaml`

```yaml
# One or more phrasings per category; a random one is picked each time so
# repeated fillers don't sound robotic (research doc §3 flags repetition as
# a "naturalness" cost — variety mitigates it cheaply).
categories:
  weather:
    criteria: "Questions about current or forecast weather, temperature, rain, snow, etc."
    phrases:
      - "Let me check the weather for you."
      - "One moment, pulling up the forecast."

  music:
    criteria: "Requests to play, pause, skip, or control music/audio."
    phrases:
      - "On it."
      - "Sure thing, one second."

  web_search:
    criteria: "General knowledge questions or requests to look something up online."
    phrases:
      - "Let me look that up."
      - "Give me a second to search for that."

  memory:
    criteria: "Requests to remember, recall, or forget something."
    phrases:
      - "Got it, one moment."
      - "Sure, let me pull that up."

  skill_install:
    criteria: "Requests to add, build, or install a new skill or capability."
    phrases:
      - "Let's set that up — one moment."
      - "Okay, give me a second to build that."

  schedule:
    criteria: "Requests about calendars, schedules, reminders, or recurring tasks."
    phrases:
      - "Let me check your schedule."
      - "One moment, checking your calendar."

  smalltalk:
    criteria: "Greetings, chit-chat, or requests with no specific task."
    phrases:
      - "Mm-hm, one sec."

  general:
    # Fallback bucket when Jev's top choice confidence is below threshold,
    # or the request doesn't fit any specific category well.
    criteria: "Anything that doesn't clearly match another category."
    phrases:
      - "One moment."
      - "Let me think about that."
```

This list is deliberately small (~6-8 categories) to stay within Jev's
`Choice` primitive's comfortable option-count range (the research/feasibility
docs note Jev handles up to 255 options, but small option sets are also
cheaper/faster and easier to keep well-separated). Categories map loosely
to skill groups (weather, music, web-search, memory, skill-install, schedule)
plus two catch-alls (smalltalk, general). `memory` and `skill_install` each
carry two phrasings (not one) so those categories don't repeat the exact
same line every time they fire — mitigating the repetition/naturalness cost
flagged in research doc §3. Extending the library later means adding a
YAML entry and running the pre-synthesis script — no code change.

### 4.3 Pre-synthesis script — `scripts/build_filler_audio.py`

New script, run manually (or as part of `run.sh` setup, see §6.5) whenever
`config/filler_phrases.yaml` changes:

- Reads `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID`
  from the environment (same vars the live TTS backend already uses — no new
  ElevenLabs config).
- For each phrase in each category, calls
  `client.text_to_speech.convert(voice_id=..., text=..., model_id=...,
  output_format="pcm_24000")` (non-streaming — one-shot synthesis is fine
  for pre-baking) and writes the raw PCM as a `.npy` float32 array (matching
  the in-memory format `_synth_audio` already produces elsewhere in
  `core/voice_backends.py`, so playback code doesn't need a separate decode
  path) to `~/.kaizen/filler_audio/<category>/<slug>.npy`.
- Idempotent: skips a phrase if its `.npy` already exists and
  `config/filler_phrases.yaml`'s mtime is older than the cache directory's
  (add a `--force` flag to regenerate everything, e.g. after a voice change).
- Prints a summary (`N phrases synthesized, M cached, category coverage:
  weather✓ music✓ ...`) so a missing category is obvious before it's needed
  at runtime.

Cache lives under `~/.kaizen/filler_audio/`, consistent with the project's
existing convention of user-scoped runtime state outside the repo
(`~/.kaizen/memory`, `~/.kaizen/models/hailo-whisper`, `~/.kaizen/sessions.db`).

## 5. Jev integration

### 5.1 New module — `core/filler_classifier.py`

```python
class FillerClassifier:
    """Classifies a transcript into a filler-phrase category via Jev.

    Network call with a hard timeout. Any failure (missing key, timeout,
    low confidence, SDK error) returns None — callers must treat None as
    "no specific filler this turn" and fall through to existing behavior.
    """

    def __init__(self, api_key: str, categories: dict, timeout_s: float = 0.5,
                 confidence_threshold: float = 0.6):
        ...

    def classify(self, transcript: str) -> str | None:
        """Return a category name, or None on timeout/error/low confidence."""
        ...
```

- Uses `typesafe_sdk.TypeSafeClient` + `Choice` per the SDK's documented
  quickstart (`state=transcript`, `criteria={name: category["criteria"] for
  ...}`).
- `timeout_s` is enforced at the HTTP client level (the SDK is expected to
  accept a `timeout` kwarg or a configurable `httpx` client — confirm against
  the installed `typesafe-sdk` version during implementation; if not
  natively supported, wrap the call in a short-lived thread with `.join(timeout)`
  and treat a still-running thread as a timeout, matching the pattern
  `main.py` already uses for wake-word watchers).
- Confidence gating: if `response.choices["category"].confidence` is below
  `confidence_threshold`, return `None` rather than the low-confidence
  choice — this is the graceful-degradation behavior the research doc calls
  out as the standard mitigation for a wrong specific filler being worse
  than a generic one (research doc §3).
- All exceptions caught and logged at `warning` level; never raises to the
  caller. This mirrors the existing failure-tolerant pattern in
  `core/session_archive.py` and the micro-tier try/except in
  `core/orchestrator.py:350-358`.

### 5.2 Config — `.env.example` additions

```bash
# Jev (TypeSafe AI) filler-phrase classifier (optional).
# Classifies the transcript into a filler-phrase category so Kaizen can
# speak a topic-relevant line ("Let me check the weather...") while the
# real Claude/tool-calling turn runs, instead of a generic beep.
# Unset, timeout, low confidence, or any error => no specific filler this
# turn (falls back to the existing thinking-sound cue). Never blocks or
# delays the real turn.
TYPESAFE_API_KEY=
FILLER_CLASSIFIER_ENABLED=true
FILLER_CLASSIFIER_TIMEOUT_MS=800
FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD=0.6
```

Add `typesafe-sdk` to `requirements.txt`.

## 6. Wiring into the voice loop

### 6.1 Where it fires

`main.py`'s conversation loop currently does (abbreviated,
`main.py:362-421`):

```python
transcription = voice.listen(
    max_wait_seconds=conversation_idle_timeout,
    on_speech_done=voice.play_thinking_sound,
)
...
response = orchestrator.process_message(transcription, ...)
```

`on_speech_done` fires **before transcription** — the transcript doesn't
exist yet, so Jev classification cannot happen there (it needs the text).
The filler classification step goes **after `voice.listen()` returns**
(transcript known) and **before `orchestrator.process_message()`** (the real,
slow call):

```python
transcription = voice.listen(
    max_wait_seconds=conversation_idle_timeout,
    on_speech_done=voice.play_thinking_sound,   # unchanged — instant "heard you"
)
...
category = filler_classifier.classify(transcription)  # ~250ms typical, 500ms hard cap
if category is not None:
    voice.play_filler(category)                        # non-blocking, like play_ack_sound
response = orchestrator.process_message(transcription, ...)  # unchanged, runs after
```

This keeps the existing instant "heard you" warble (fires at end-of-speech,
before STT even finishes) and *adds* a second, topic-specific spoken filler
right after STT completes and before the slow Claude call starts. Two cues
per turn (warble, then spoken filler) is consistent with the existing
multi-cue precedent documented in the 2026-06-18 spec (warble → response-ready
chirp already happens today); this replaces the second cue's *content* on
turns where Jev succeeds, from a chirp to actual words.

`filler_classifier.classify()` runs on the main thread, synchronously, with
its own hard timeout — it does not need its own background thread, because
`voice.play_filler()` (like `play_ack_sound`) is a non-blocking `sd.play`
call, and everything after it (`process_message`) already dominates total
turn time by 4-10x. Worst case (timeout hit, no category) costs exactly
`FILLER_CLASSIFIER_TIMEOUT_MS` of added latency before the real call starts
— this is the one deliberate risk this design accepts, and is why the
timeout defaults low (800ms) and is user-configurable.

> **Revised 2026-09-24:** the original 500ms default was bumped to 800ms
> after real-world testing showed cold-start connection warm-up overhead
> pushed first-call latency in a session to ~505ms, which exceeded the old
> timeout. Steady-state calls measured 137-422ms, comfortably within the
> new 800ms budget.

### 6.2 New `VoiceInterface` method — `play_filler(category: str)`

`core/voice.py`, alongside `play_ack_sound` / `play_thinking_sound`:

```python
def play_filler(self, category: str) -> None:
    """Play a pre-cached filler phrase for `category`. Picks a random
    phrase from the category's cache directory. No-op (logged) if the
    category has no cached audio — never raises, never blocks."""
```

- Loads a random `.npy` from `~/.kaizen/filler_audio/<category>/`.
- Resamples to `self._output_samplerate` (same `resample()` helper every
  other cue already uses) and `sd.play`s it non-blocking — identical
  pattern to `play_ack_sound` (`core/voice.py:509-530`), just with a loaded
  file instead of a synthesized chirp.
- Missing category directory / empty directory / file read error → logs a
  warning and returns silently (matches every other `play_*` method's
  failure-tolerance convention in this file).

### 6.3 Interaction with barge-in and the response-ready cue

No changes needed to barge-in or `play_response_ready_sound` — the filler
plays and finishes (a few seconds of speech) well before Claude's answer is
ready in the typical case; if the real answer *is* unusually fast (a cached
or trivial turn), `sd.play`'s non-blocking nature and PipeWire's mixing
already handles overlapping cues today (documented behavior from the
2026-06-18 spec, §A.3: "PipeWire mixes them. Acceptable.").

### 6.4 Future fallback sketch (not built in v1)

If Jev proves too slow/unreliable in practice, the natural extension is a
regex pre-check identical in shape to `TierRouter`'s dispatch table: try a
fast regex match against `config/filler_phrases.yaml` categories' known
trigger words first; only call Jev when regex doesn't match. This is
additive and doesn't require restructuring anything in this spec — flagged
here so the `FillerClassifier` interface (`classify() -> str | None`) is
written generically enough that a `RegexFillerClassifier` could implement
the same interface later and be composed via try-this-then-that, mirroring
the existing micro-tier-then-Sonnet escalation pattern.

### 6.5 Setup / build step

`scripts/build_filler_audio.py` needs to run once (and after any
`config/filler_phrases.yaml` edit) before the feature works. Document in
`README.md`'s Quick Start alongside the existing `.env` setup step; do not
wire it into `run.sh`'s auto-build (unlike Docker containers) since it needs
a live ElevenLabs key and network access at setup time — a missing cache
should degrade to "no filler this turn," not block startup.

## 7. Failure modes

| Failure | Handled by |
|---|---|
| `TYPESAFE_API_KEY` unset / `FILLER_CLASSIFIER_ENABLED=false` | `FillerClassifier` never constructed; `main.py` skips the classify+play_filler step entirely — identical to today's behavior. |
| Jev API timeout (>500ms) | `classify()` returns `None`; no filler plays this turn; real turn proceeds unaffected (only cost: the timeout duration itself, bounded). |
| Jev API error (network, 5xx, invalid key) | Caught, logged at `warning`, returns `None`. Same as timeout. |
| Jev returns a category with confidence below threshold | Treated as `None` — no specific filler; falls through to existing generic thinking-sound cue (already played at `on_speech_done`, no change needed). |
| Category has no cached audio (missing/failed pre-synthesis) | `play_filler` logs a warning and no-ops; does not fall back to a different category (avoids compounding a classification uncertainty with a substitution guess). |
| `scripts/build_filler_audio.py` never run | All categories missing cached audio → feature is silently inert (every `play_filler` call no-ops) until the script is run. Consider a startup log line noting `0/N filler categories cached` if `FILLER_CLASSIFIER_ENABLED=true`, so this isn't silently broken. |
| ElevenLabs key rotates/expires between pre-synthesis runs | Only affects `build_filler_audio.py` (a setup-time script), not the runtime path — cached `.npy` files keep working regardless of current key validity. |

## 8. Testing

No live API calls in the test suite (matches existing project convention —
see the ElevenLabs backend's mocked-SDK test approach).

- **`tests/test_filler_classifier.py`** (new):
  - `classify()` returns the expected category when the (mocked)
    `TypeSafeClient` returns a high-confidence choice.
  - `classify()` returns `None` when confidence is below threshold.
  - `classify()` returns `None` and logs a warning when the client raises.
  - `classify()` returns `None` when the call exceeds `timeout_s` (mock a
    slow response / use the thread-join pattern's timeout path).
  - Disabled (`FILLER_CLASSIFIER_ENABLED=false` or no API key) → classifier
    is not constructed / `classify` is never called by the voice loop
    (assert via a spy on `main.py`'s wiring, similar to the existing
    `on_speech_done` wiring test in `tests/test_voice_mode.py`).
- **`tests/test_voice_sounds.py`** (extend):
  - `play_filler("weather")` loads and plays a stubbed cached array via
    `sd.play` when the category directory exists.
  - `play_filler("nonexistent")` logs a warning and does not raise or call
    `sd.play`.
- **`scripts/build_filler_audio.py`** — a small unit test (mocked ElevenLabs
  client) verifying: idempotency (doesn't re-synthesize an already-cached
  phrase), `--force` regenerates, and the summary output correctly reports
  per-category coverage.
- **Integration point in `tests/test_voice_mode.py`**: extend the existing
  fake-voice-loop harness to assert `play_filler` is called with the
  classifier's returned category before `process_message`, and NOT called
  when the classifier returns `None`.

## 9. Out of scope (this spec)

- Regex/local fallback classifier (§6.4 — sketched, not built).
- Laya or any other local classifier (rejected, see `docs/laya-feasibility-report.md`).
- Per-utterance runtime fallback for the *real* ElevenLabs answer synthesis
  (unrelated, already out of scope per the 2026-07-23 ElevenLabs spec).
- Dynamic/LLM-generated filler phrases (library is static, hand-authored;
  revisit only if the fixed library feels repetitive in practice — the
  research doc's ConvFill citation is the relevant prior art if this is
  ever revisited).
- Multi-language filler phrases (English only, matching the rest of the
  project's current scope).

## 10. Open questions for Mason

1. Confirm the phased approach (Jev-only v1, regex fallback deferred) vs.
   building both now.
2. Confirm `FILLER_CLASSIFIER_TIMEOUT_MS=800` (revised from 500 after
   real-world testing showed cold-start connection overhead pushed
   first-call latency to ~505ms) and
   `FILLER_CLASSIFIER_CONFIDENCE_THRESHOLD=0.6` as starting defaults — these
   are reasonable priors from the research, not measured on your hardware/
   account yet.
3. Confirm the initial category list in §4.2, or provide your own — these
   were drafted from the existing skill set (weather, music, web-search,
   memory, skill-install) plus two catch-alls.
