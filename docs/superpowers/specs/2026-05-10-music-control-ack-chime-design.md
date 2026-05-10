# Music-control ack chime (replace TTS speech with a short audio cue)

**Date:** 2026-05-10
**Status:** Approved, pending implementation plan
**Owner:** Mason

## Goal

When a deterministic-tier voice command lands on a music-control transport success ("pause", "resume", "skip", "volume up/down", "stop"), MiniClaw should play a short chime instead of speaking "Paused." / "Resumed." / etc. Errors and clarifications stay spoken.

## Background

`_music_control_soundcloud` and `_music_control_spotify` return exactly six success strings: `"Paused."`, `"Resumed."`, `"Skipped."`, `"Volume up."`, `"Volume down."`, `"Stopped."`. These commands route through the deterministic tier (regex match in `config/intent_patterns.yaml`), so the skill's return string is spoken verbatim by Kokoro. Spoken confirmation is unnecessary noise for tight transport feedback — a chime communicates "got it, did the thing" faster.

## Non-goals

- Chiming for non-music skills (save-memory's "Got it.", schedule's confirmations, etc.). Out of scope.
- Chiming for initial play commands (`"Now playing: <track>"`). Those are informative, not transport.
- Changing how error messages are delivered (`"Nothing is playing."`, `"Spotify Connect device unavailable."`, `"Couldn't reach Spotify: ..."` stay spoken).
- Affecting text mode. Terminal output remains `"Paused."` etc.
- Changing what Claude says when the transcript is garbled (Claude-handled clarification path is untouched because garbled input never matches the deterministic regex).

## Design

### Components

**1. `core/voice.py` — new method `play_ack_sound()`**

Short rising two-tone blip (~150ms total), R2-D2 family, synthesised with numpy + sounddevice (matches existing `play_startup_sound` / `play_thinking_sound` / `play_response_ready_sound` style — no audio file dependency).

**2. `core/orchestrator.py` — `on_ack_success` callback parameter**

`process_message` and `_execute_direct` gain an optional `on_ack_success: Callable[[], None] | None = None` parameter. Inside `_execute_direct`, after computing `result`:

```python
ACK_SUCCESS = {
    "Paused.", "Resumed.", "Skipped.",
    "Volume up.", "Volume down.", "Stopped.",
}
if result in ACK_SUCCESS and on_ack_success is not None:
    on_ack_success()
    return ""
if on_chunk is not None and result:
    on_chunk(result)
return result
```

Empty-string return is the signal "TTS handled by side channel, don't speak."

**3. `main.py` (voice-mode loop) — wire it up**

Both streaming and non-streaming branches pass `on_ack_success=voice.play_ack_sound`. After the response comes back, skip `voice.speak(response)` (and skip the streaming `finalize()`) when `response == ""`. The terminal print still happens, but it'll print an empty line (acceptable) or we can suppress the empty print explicitly.

The `ACK_SUCCESS` set lives in `core/container_manager.py` (next to the methods that produce these strings) as a module constant exported for the orchestrator to import. Single source of truth.

### Flow

1. User says "pause."
2. TierRouter regex matches → deterministic tier → `_execute_direct` → `container_manager.execute_skill("music-control", {"action": "pause"})` → `_music_control_spotify("pause")` → returns `"Paused."`.
3. `_execute_direct` sees `"Paused." in ACK_SUCCESS`, calls `on_ack_success()` (which is `voice.play_ack_sound`), returns `""`.
4. main.py receives `""`, skips TTS, prints (optionally an empty line, or nothing).
5. User hears a chime ~50ms after they finished speaking. No "Paused." utterance.

## Failure modes

| Failure | Handled by |
|---------|------------|
| `_music_control_*` wording drift (e.g. someone changes `"Paused."` → `"Pause confirmed."`) | Unit test pins the exact ACK_SUCCESS set; the wording change must update both sides or the test fails. |
| Result is `"Nothing is playing."` (or any other non-success string) | Not in ACK_SUCCESS → falls through to normal `on_chunk` / `voice.speak` path → user hears spoken explanation. |
| Garbled transcript | Doesn't match the deterministic regex → routes to Haiku/Sonnet → clarification spoken normally. |
| Text mode (`main.py` text branch) | Voice-mode branch is the only one wiring `on_ack_success`. Text mode prints `"Paused."` to terminal as before. |
| `voice.play_ack_sound` itself raises | Wrap in try/except in the orchestrator? No — the existing `play_*_sound` helpers don't crash on Pi audio. If they did, the orchestrator's existing exception handling at the voice-loop level catches it and continues. Don't over-engineer. |

## Testing

Add to `tests/test_music_control.py` (or a new `tests/test_ack_success.py`):

1. **`_execute_direct` calls `on_ack_success` and returns `""` when result is in `ACK_SUCCESS`.** Mock the container_manager to return `"Paused."`; pass a mock callback; assert it's called once and `_execute_direct` returns `""`.
2. **`_execute_direct` falls through to `on_chunk` when result is *not* in `ACK_SUCCESS`.** Same setup but result is `"Nothing is playing."`; assert callback NOT called and `on_chunk` called with the string.
3. **`ACK_SUCCESS` constant matches the six known transport returns.** Pin the set explicitly — if `_music_control_spotify` adds a new success string, this test forces a deliberate decision about whether it should chime.
4. **`voice.play_ack_sound` is a callable on VoiceInterface.** Smoke test that the method exists and runs without raising on a stubbed audio backend.

## Open questions

None.

## Risks

- **The "empty response" signal is slightly magical.** A future refactor that switches `process_message` to return something other than `str` would break the convention. Mitigation: a one-line comment at the empty-string return site documents the contract.
- **Synth quality of the chime.** R2-D2-style synth on the Pi sometimes clips through PipeWire-pulse if the buffer config drifts. Reuse the same `sounddevice` pattern as existing helpers; if it sounds wrong, tune later.
