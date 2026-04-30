# Elevator Music During the Wait

**Status:** Spec
**Date:** 2026-04-30
**Owner:** Mason Misch

## Problem

End-to-end voice latency on the Pi is several seconds per turn. Until
the profiling pass (separate plan) drives real fixes, the user has no
audio feedback during the wait — there's a brief R2-D2 chirp at the
start, then silence until TTS begins. Silent waits feel longer than
they are and don't communicate "the assistant is working."

## Goal

Replace the silent wait with looping elevator-style music that starts
when the user finishes speaking and stops abruptly when the response is
ready. The bit only works if the cut is clean; mask the latency *and*
get a laugh.

## Decisions

- **Audio source:** one WAV file bundled in the repo at
  `assets/elevator.wav`. No env-var path override.
- **Format:** 16-bit PCM WAV, 44.1 kHz, mono. Decoded via stdlib `wave`
  and `numpy`; no new audio dependencies.
- **License:** CC0 / public domain preferred. If I can only source a
  CC-BY clip, surface the license and add an attribution line to the
  README before bundling.
- **Stop behavior:** hard cut. Music stops, then TTS opens a fresh
  `sounddevice.OutputStream`.
- **R2-D2 thinking chirp:** removed entirely from the voice loop. Music
  is the new "I heard you, hold on."
- **Default state:** ON. Disable with `MINICLAW_ELEVATOR_MUSIC=false`.
- **Failure mode:** silent fallback. Asset missing or audio-out errors
  log a warning and disable the feature for the session — the assistant
  must never crash because the music failed.

## Architecture

Two new methods on `Voice` in `core/voice.py`:

```python
def start_thinking_music(self) -> None: ...
def stop_thinking_music(self) -> None: ...
```

Both are no-ops when:

- `MINICLAW_ELEVATOR_MUSIC` is `false` (case-insensitive),
- `self.enable_tts` is false (existing master audio gate),
- the asset failed to load at init.

A daemon thread plays `sd.play(buf, ...) ; sd.wait()` in a loop while
a `_music_playing` flag is set. `stop_thinking_music()` clears the
flag, calls `sd.stop()` to interrupt the in-flight clip, and joins the
thread with a short timeout. Idempotent — safe to call when not started.

## Components

### `Voice.__init__` additions

- The asset path lives at a module-level constant in `core/voice.py`:
  `_MUSIC_ASSET_PATH = Path(__file__).parent.parent / "assets" / "elevator.wav"`.
  Tests monkeypatch this constant to point at a fixture or to a
  nonexistent path.
- Read `MINICLAW_ELEVATOR_MUSIC` from env (default `true`).
- If enabled and `enable_tts` is true: load `_MUSIC_ASSET_PATH`,
  decode to a numpy `float32` mono array, resample to
  `self._output_samplerate`, cache on `self._music_buffer`.
- On any load failure: log a single warning, set `self._music_buffer = None`,
  continue. Methods will check the buffer and no-op when missing.

### `Voice.start_thinking_music`

```python
def start_thinking_music(self) -> None:
    if not self.enable_tts or self._music_buffer is None:
        return
    if self._music_thread is not None and self._music_thread.is_alive():
        return  # already playing
    self._music_playing = True
    self._music_thread = threading.Thread(
        target=self._music_loop, daemon=True
    )
    self._music_thread.start()
```

### `Voice._music_loop`

```python
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
```

### `Voice.stop_thinking_music`

```python
def stop_thinking_music(self) -> None:
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

### Voice loop edit in `main.py`

Inside the `with profiling.turn():` block, where today the loop calls
`voice.play_thinking_sound()`:

```python
# Before:
voice.play_thinking_sound()
response = orchestrator.process_message(transcription)
print(f"Assistant: {response}\n")
with profiling.stage("tts"):
    voice.speak(response)

# After:
voice.start_thinking_music()
try:
    response = orchestrator.process_message(transcription)
finally:
    voice.stop_thinking_music()
print(f"Assistant: {response}\n")
with profiling.stage("tts"):
    voice.speak(response)
```

`try/finally` guarantees the music stops if `process_message` raises —
otherwise a Ctrl+C mid-LLM would leave the music looping until the
daemon thread dies on process exit.

`play_thinking_sound` is no longer called from `main.py` but stays
defined on `Voice` for now — out-of-scope to delete; will fall out in a
future cleanup if it stays unused.

### Asset

- `assets/elevator.wav` — committed to the repo. ~10–30 seconds is
  enough; the loop covers any wait length.
- The repo currently has no `assets/` directory; create it. (Not the
  agentskills `assets/` convention inside a skill — this is the
  project root.)
- Track size budget: target under 5 MB. A 30-second 44.1 kHz mono
  16-bit WAV is ~2.5 MB.
- README gets a line near the credits/footer noting the source and
  license. If CC-BY, the attribution text required by the license goes
  there verbatim.

## Data Flow

```
listen() → transcription
  if exit-words → close session
voice.start_thinking_music()      # spawns daemon thread, returns immediately
  response = orchestrator.process_message(transcription)
voice.stop_thinking_music()       # sd.stop(), join thread
voice.speak(response)             # TTS opens its own OutputStream cleanly
```

Audio device is owned by exactly one stream at a time:

1. start_thinking_music opens via `sd.play`.
2. stop_thinking_music closes via `sd.stop` + thread join.
3. `voice.speak` then opens the Kokoro `OutputStream` — no contention
   because step 2 fully completed first.

## Error Handling

| Failure                                    | Behavior                                                 |
|--------------------------------------------|----------------------------------------------------------|
| `assets/elevator.wav` missing              | Warn once at init; `_music_buffer = None`; methods no-op |
| WAV decode raises                          | Same as above                                            |
| `sd.play` raises in loop                   | Warn, exit the music thread; future calls re-spawn       |
| `sd.stop` raises                           | Warn, continue; thread join still happens                |
| Thread doesn't exit within 0.5s join       | Continue anyway; daemon thread won't block process exit  |
| `process_message` raises                   | `try/finally` in main.py stops music                     |
| Env-disabled (`MINICLAW_ELEVATOR_MUSIC=false`) | start/stop are unconditional no-ops; no thread, no audio |
| TTS globally disabled (`enable_tts=False`) | Same as above                                            |

## Testing

Unit tests in `tests/test_voice_elevator_music.py` (new file). Use
`monkeypatch` and a stubbed `sounddevice` to avoid real audio.

| Test                                              | Assertion                                                          |
|---------------------------------------------------|--------------------------------------------------------------------|
| `test_disabled_by_env_is_noop`                    | `MINICLAW_ELEVATOR_MUSIC=false` → `start_thinking_music` does nothing |
| `test_tts_disabled_is_noop`                       | `enable_tts=False` → `start_thinking_music` does nothing           |
| `test_missing_asset_logs_and_disables`            | Monkeypatch `_MUSIC_ASSET_PATH` to a nonexistent file → init warns, `_music_buffer is None` |
| `test_start_then_stop_no_raise`                   | Stub sd.play/wait/stop; full cycle completes cleanly               |
| `test_stop_without_start_is_noop`                 | Calling `stop_thinking_music` without prior start does not raise   |
| `test_double_start_only_spawns_one_thread`        | Two consecutive `start_thinking_music` calls → one thread          |

No real-audio integration test on CI (audio out unavailable in CI).
Manual validation on the Pi: voice mode, ask anything, music plays
during wait, stops when answer arrives.

## Asset Sourcing

Order of preference:

1. CC0 / public-domain elevator/muzak loop from a reputable source
   (e.g. opengameart.org, freesound.org with CC0 filter, archive.org
   public-domain collections).
2. CC-BY clip — bundle if (1) yields nothing usable, with attribution
   in `README.md`.
3. If neither pans out before the implementation step, stop and ask the
   user how to proceed (e.g. provide their own file).

The asset is committed in the same PR as the code so the feature works
end-to-end on first install.

## Out of Scope

- Volume control, fade-out, ducking under TTS — rejected during
  brainstorm; hard cut only.
- Multiple-clip pool / random selection.
- Per-route music (different bed for Ollama vs Claude turns).
- `MINICLAW_ELEVATOR_PATH` env var to override the bundled file —
  rejected; one file is enough for v1, add later if asked.
- Removing the now-unused `play_thinking_sound` method from `Voice`.
