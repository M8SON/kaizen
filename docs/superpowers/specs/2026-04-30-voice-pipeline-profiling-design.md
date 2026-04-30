# Voice Pipeline Profiling

**Status:** Spec
**Date:** 2026-04-30
**Owner:** Mason Misch

## Problem

End-to-end voice latency on the Pi feels slow at every stage, but we don't
know which stages are the actual offenders. Optimizing without measurement
risks chasing the wrong bottleneck (e.g. tuning Kokoro when Ollama
cold-load is the real cost). We need lightweight, opt-in timing
instrumentation to produce a clear per-turn breakdown that future
optimization work can target.

## Goal

Capture wall-clock duration at each stage of one voice turn and emit a
single, greppable summary line per turn when profiling is enabled.

## Non-Goals

- No fixes. This spec ships measurement only. Optimization is a follow-up.
- No persistent metrics store, dashboard, or histogram. One log line per
  turn is sufficient for hand-eyeballing the breakdown.
- No always-on overhead. Profiling is gated by env var.

## Approach

Add a small `Timer` context manager in `core/profiling.py`. Each
instrumented call site wraps its work in `Timer("stage_name")`, which
records the duration into a per-turn dict. The orchestrator emits one
`[TIMING-SUMMARY]` log line at the end of `process_message` containing
all stages observed during that turn.

Gated by `MINICLAW_PROFILE=true`. When unset (default), the Timer is a
no-op and no log lines are emitted, so production runs pay nothing.

## Stages Instrumented

| Stage              | Where                          | Description                                              |
|--------------------|--------------------------------|----------------------------------------------------------|
| `wake_to_listen`   | `core/voice.py`                | Wake-detected → recording start                          |
| `record_duration`  | `core/voice.py`                | Silence-trimmed user-speech length                       |
| `stt`              | `core/voice.py`                | Whisper / Hailo transcribe call                          |
| `tier_route`       | `core/tier_router.py`          | `classify()` decision                                    |
| `llm_ollama`       | `core/ollama_tool_loop.py`     | Each Ollama call (second+ occurrence gets `_2`, `_3`, ...) |
| `llm_claude`       | `core/orchestrator.py`         | Each Claude call (second+ occurrence gets `_2`, `_3`, ...) |
| `tool_<skill>`     | `core/container_manager.py`    | Per skill execution (Docker boot + run, or native)       |
| `tts_first_chunk`  | `core/voice.py`                | Silence-end → first audio sample to speaker              |
| `total`            | `main.py` / orchestrator entry | Silence-end → first audio chunk (user-perceived wait)    |

If a stage occurs more than once in a turn (e.g. two Ollama calls or two
tool invocations), the first keeps its plain name and subsequent
occurrences are suffixed `_2`, `_3`, ... so each appears in the summary.

## Output Format

One INFO-level log line per turn when `MINICLAW_PROFILE=true`:

```
[TIMING-SUMMARY] stt=412 tier=3 llm_ollama=3540 tool_weather=1280 llm_claude=2100 tts_first=290 total=7625
```

All values in integer milliseconds. Order: stages in the order they
fired during the turn. Easy to grep, paste, and diff.

## Implementation Sketch

`core/profiling.py` (~40 lines):

```python
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

_logger = logging.getLogger(__name__)
_enabled = os.environ.get("MINICLAW_PROFILE", "").lower() == "true"
_current_turn: ContextVar[dict | None] = ContextVar("current_turn", default=None)


@contextmanager
def turn():
    """Open a per-turn timing scope. Emits the summary line on exit."""
    if not _enabled:
        yield
        return
    stages: dict[str, int] = {}
    token = _current_turn.set(stages)
    try:
        yield stages
    finally:
        _current_turn.reset(token)
        if stages:
            line = " ".join(f"{k}={v}" for k, v in stages.items())
            _logger.info("[TIMING-SUMMARY] %s", line)


@contextmanager
def stage(name: str):
    """Time a stage. No-op when profiling is disabled or no turn is open."""
    if not _enabled:
        yield
        return
    stages = _current_turn.get()
    if stages is None:
        yield
        return
    # Disambiguate repeated stages within a turn.
    final = name
    n = 1
    while final in stages:
        n += 1
        final = f"{name}_{n}"
    t0 = time.perf_counter()
    try:
        yield
    finally:
        stages[final] = int((time.perf_counter() - t0) * 1000)
```

Call-site usage:

```python
from core import profiling

with profiling.stage("stt"):
    text = whisper.transcribe(audio)
```

`main.py` opens the per-turn scope around `orchestrator.process_message`
so all stage records land in the same summary.

## Files Touched

- **new:** `core/profiling.py`
- **edit:** `core/voice.py`, `core/orchestrator.py`, `core/tier_router.py`,
  `core/ollama_tool_loop.py`, `core/container_manager.py`, `main.py`
- **no test changes** — observability code, no behavior change when
  `MINICLAW_PROFILE` is unset

## Validation Plan

1. Set `MINICLAW_PROFILE=true` in the Pi `.env`.
2. Restart MiniClaw.
3. Run three voice turns:
   - direct route (e.g. "what time is it" → handled deterministically)
   - Ollama route (e.g. "tell me a joke" → phi4-mini)
   - tool call route (e.g. "what's the weather" → weather skill)
4. Capture the three `[TIMING-SUMMARY]` lines.
5. Use the breakdown to drive a follow-up brainstorm on actual fixes.

## Risks

- Timing under heavy CPU load can drift; perf_counter is monotonic and
  fine, but Hailo/Whisper threads may warp wall-clock perception of
  individual stages. Treat numbers as directional, not micro-precise.
- ContextVar inheritance across thread boundaries is a known footgun.
  All voice-pipeline work currently runs on the main thread, so this is
  not a problem today, but if any stage moves to a worker thread later,
  the Timer there will silently skip recording. Acceptable for now.

## Out of Scope (revisit after measurement)

- Hailo wake-window rework (memory item: known unusable in current form)
- Ollama keep-alive tuning
- Docker container preloading / warm pool
- Skill-prompt pruning
- Kokoro warm-up / streaming improvements

These will be designed in a follow-up brainstorm once we have numbers.
