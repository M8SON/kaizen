# Voice Pipeline Profiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, low-overhead per-stage timing to the voice pipeline that emits one greppable `[TIMING-SUMMARY]` log line per turn, so we can identify the real latency bottlenecks before designing fixes.

**Architecture:** A small `core/profiling.py` exposes two context managers — `turn()` opens a per-turn scope (a `ContextVar[dict]`) that collects stage durations and logs the summary on exit, and `stage(name)` measures a single block. Both are no-ops when `MINICLAW_PROFILE` is not `true`. Call sites in `voice.py`, `tier_router.py`, `orchestrator.py`, `ollama_tool_loop.py`, and `container_manager.py` wrap their work in `stage(...)`. `main.py` (voice loop) and `orchestrator.process_message` open the `turn()` scope.

**Tech Stack:** Python 3.12, `contextvars.ContextVar`, `time.perf_counter`, stdlib `logging`, pytest.

**Spec:** `docs/superpowers/specs/2026-04-30-voice-pipeline-profiling-design.md`

---

## File Structure

- **new:** `core/profiling.py` — two context managers (`turn`, `stage`), env-gated
- **new:** `tests/test_profiling.py` — unit tests for the module
- **edit:** `core/voice.py` — wrap STT call (`_run_transcribe` or equivalent) and the silence-end → first-TTS-chunk window
- **edit:** `core/tier_router.py` — wrap `route()` body
- **edit:** `core/ollama_tool_loop.py` — wrap each Ollama HTTP call
- **edit:** `core/orchestrator.py` — wrap `messages.create()` call(s); open `turn()` around `process_message`
- **edit:** `core/container_manager.py` — wrap `execute_skill` body with `stage(f"tool_{skill_name}")`
- **edit:** `main.py` — replace the ad-hoc `t_*` timing block with `profiling.turn()` framing; record `wake_to_listen`, `record_duration`, `total`

---

## Task 1: Profiling module + tests

**Files:**
- Create: `core/profiling.py`
- Create: `tests/test_profiling.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_profiling.py
import logging
import time

import pytest

from core import profiling


def test_disabled_by_default(monkeypatch, caplog):
    monkeypatch.delenv("MINICLAW_PROFILE", raising=False)
    profiling._refresh_enabled()  # re-read env after monkeypatch
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("foo"):
                pass
    assert "[TIMING-SUMMARY]" not in caplog.text


def test_records_stage_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("foo"):
                time.sleep(0.01)
    assert "[TIMING-SUMMARY]" in caplog.text
    assert "foo=" in caplog.text


def test_repeat_stage_is_suffixed(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("call"):
                pass
            with profiling.stage("call"):
                pass
            with profiling.stage("call"):
                pass
    assert "call=" in caplog.text
    assert "call_2=" in caplog.text
    assert "call_3=" in caplog.text


def test_stage_outside_turn_is_silent(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.stage("orphan"):
            pass
    # No turn open → no summary line emitted, no crash.
    assert "[TIMING-SUMMARY]" not in caplog.text


def test_empty_turn_emits_no_line(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            pass
    assert "[TIMING-SUMMARY]" not in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_profiling.py -v`
Expected: FAIL — `core.profiling` module does not exist (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `core/profiling.py`**

```python
"""
Per-turn stage timing for the voice pipeline.

Gated by MINICLAW_PROFILE=true. When disabled, both context managers are
true no-ops and add zero measurable overhead. When enabled, each turn
emits a single INFO log line of the form:

    [TIMING-SUMMARY] stt=412 tier=3 llm_ollama=3540 tool_weather=1280 total=7625
"""

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_enabled = False
_current_turn: ContextVar["dict[str, int] | None"] = ContextVar(
    "current_turn", default=None
)


def _refresh_enabled() -> None:
    """Re-read MINICLAW_PROFILE from the environment. Called at import
    time and exposed so tests can flip the flag via monkeypatch."""
    global _enabled
    _enabled = os.environ.get("MINICLAW_PROFILE", "").strip().lower() == "true"


_refresh_enabled()


@contextmanager
def turn():
    """Open a per-turn timing scope. Emits the summary on exit."""
    if not _enabled:
        yield None
        return
    stages: dict[str, int] = {}
    token = _current_turn.set(stages)
    try:
        yield stages
    finally:
        _current_turn.reset(token)
        if stages:
            line = " ".join(f"{k}={v}" for k, v in stages.items())
            logger.info("[TIMING-SUMMARY] %s", line)


@contextmanager
def stage(name: str):
    """Time a stage. No-op when disabled or when no turn() is open."""
    if not _enabled:
        yield
        return
    stages = _current_turn.get()
    if stages is None:
        yield
        return
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

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_profiling.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add core/profiling.py tests/test_profiling.py
git commit -m "feat(profiling): per-turn stage timer module

Adds core/profiling.turn() and core/profiling.stage() context
managers gated by MINICLAW_PROFILE=true. No-ops when disabled."
```

---

## Task 2: Wrap STT in voice.py

**Files:**
- Modify: `core/voice.py` (find the body of `listen()` around line 168 and the underlying `whisper.transcribe` / hailo transcribe call inside it)

- [ ] **Step 1: Read voice.py to locate the actual STT call**

Run: `grep -n "transcribe\|recognize\|stt" core/voice.py`

Identify the single line (or block) that runs the model on captured audio. There may be one path for whisper and another for the Hailo backend; if both exist, wrap both with `profiling.stage("stt")` — they cannot fire in the same turn.

- [ ] **Step 2: Add the import at the top of `voice.py`**

```python
from core import profiling
```

- [ ] **Step 3: Wrap the transcribe call(s)**

Example (the exact line numbers will differ — keep the wrap minimal and local to the model call):

```python
# Before:
text = self._whisper.transcribe(audio_bytes)

# After:
with profiling.stage("stt"):
    text = self._whisper.transcribe(audio_bytes)
```

If both the CPU-Whisper and Hailo paths exist as separate methods, wrap each at the actual model-invocation line, not at the dispatch site, so the timing reflects the model's own work.

- [ ] **Step 4: Verify tests still pass**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add core/voice.py
git commit -m "feat(profiling): time STT in voice.listen()"
```

---

## Task 3: Wrap router decision in tier_router.py

**Files:**
- Modify: `core/tier_router.py` — `route()` method at line 82

- [ ] **Step 1: Add the import**

At the top of `core/tier_router.py`:

```python
from core import profiling
```

- [ ] **Step 2: Wrap the body of `route()`**

```python
def route(self, transcript: str) -> RouteResult:
    with profiling.stage("tier_route"):
        # ... existing body unchanged ...
```

Keep the wrap *inside* `route()` so callers don't need to change.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tier_router.py -v`
Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add core/tier_router.py
git commit -m "feat(profiling): time TierRouter.route()"
```

---

## Task 4: Wrap each Ollama HTTP call in ollama_tool_loop.py

**Files:**
- Modify: `core/ollama_tool_loop.py` around line 121 (the `requests.post` call inside the `run()` loop)

- [ ] **Step 1: Add the import**

At the top of `core/ollama_tool_loop.py`:

```python
from core import profiling
```

- [ ] **Step 2: Wrap the HTTP call**

```python
# Before:
try:
    response = requests.post(
        f"{self.host}/v1/chat/completions",
        json=payload,
        timeout=self.timeout,
    )
    response.raise_for_status()
except requests.Timeout:
    ...

# After:
try:
    with profiling.stage("llm_ollama"):
        response = requests.post(
            f"{self.host}/v1/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
except requests.Timeout:
    ...
```

The `stage()` is *inside* the `try:` so a failed/raised call still records the elapsed time. Multiple iterations of the run-loop produce `llm_ollama`, `llm_ollama_2`, ... automatically.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_ollama_tool_loop.py -v`
Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add core/ollama_tool_loop.py
git commit -m "feat(profiling): time each Ollama call"
```

---

## Task 5: Wrap each Claude call in orchestrator.py + open turn() scope

**Files:**
- Modify: `core/orchestrator.py` — `messages.create()` at line 371; `process_message()` at line 278

- [ ] **Step 1: Add the import**

At the top of `core/orchestrator.py`:

```python
from core import profiling
```

- [ ] **Step 2: Wrap the Claude API call**

```python
# Before:
response = self.client.messages.create(
    ...
)

# After:
with profiling.stage("llm_claude"):
    response = self.client.messages.create(
        ...
    )
```

If `messages.create` is invoked from more than one site in this file, wrap each one. Repeats within a single turn auto-suffix to `llm_claude_2`, etc.

- [ ] **Step 3: Open the turn() scope around process_message**

```python
def process_message(self, user_message: str) -> str:
    with profiling.turn():
        # ... existing body unchanged ...
```

This ensures every stage fired during reasoning + tool calls lands in one summary line.

- [ ] **Step 4: Run orchestrator tests**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_routing.py tests/test_orchestrator_archive.py tests/test_orchestrator_checkpoint.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py
git commit -m "feat(profiling): time Claude calls + open per-turn scope"
```

---

## Task 6: Wrap skill execution in container_manager.py

**Files:**
- Modify: `core/container_manager.py` — `execute_skill()` at line 102

- [ ] **Step 1: Add the import**

At the top of `core/container_manager.py`:

```python
from core import profiling
```

- [ ] **Step 2: Wrap the body of execute_skill**

```python
def execute_skill(self, skill, tool_input: dict) -> str:
    skill_name = getattr(skill, "name", "unknown")
    with profiling.stage(f"tool_{skill_name}"):
        # ... existing body unchanged ...
```

The wrap covers both the Docker path and the native path so we get apples-to-apples comparison. The `skill_name` substitution lets us see which specific skill is slow (e.g. `tool_weather=1280`).

- [ ] **Step 3: Run container manager tests**

Run: `.venv/bin/python -m pytest tests/test_container_manager.py -v`
Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add core/container_manager.py
git commit -m "feat(profiling): time skill execution per skill name"
```

---

## Task 7: Voice loop framing in main.py

**Files:**
- Modify: `main.py` voice-mode loop (the block around line 196 that already does ad-hoc `t_*` timing)

- [ ] **Step 1: Read the current ad-hoc timing block**

Run: `grep -n "t_listen_start\|t_llm\|t_speak\|listen=\|process_message" main.py`

Identify the block that currently prints `[timing] listen=... llm=... tts=... total=...`. We are going to replace it with `profiling.stage()` records, so the same data ends up in the unified summary.

- [ ] **Step 2: Add the import**

At the top of `main.py`:

```python
from core import profiling
```

- [ ] **Step 3: Replace the ad-hoc timing with profiling.stage() wraps**

The existing pattern looks roughly like:

```python
t_listen_start = time.monotonic()
transcription = voice.listen(...)
t_listen_end = time.monotonic()
...
voice.play_thinking_sound()
t_llm_start = time.monotonic()
response = orchestrator.process_message(transcription)
t_llm_end = time.monotonic()
print(f"Assistant: {response}\n")
voice.speak(response)
t_speak_end = time.monotonic()
print(f"[timing] listen=... llm=... tts=... total=...")
```

Replace with:

```python
with profiling.stage("listen_record"):
    transcription = voice.listen(...)
# ... existing exit-word / empty-transcription handling ...

voice.play_thinking_sound()
# process_message opens its own profiling.turn(); the stages below
# attach to that turn because they run on the same thread/context.
response = orchestrator.process_message(transcription)
print(f"Assistant: {response}\n")
with profiling.stage("tts"):
    voice.speak(response)
```

Delete the old `t_*` variables and the `[timing]` print.

**Important:** `profiling.turn()` is opened inside `orchestrator.process_message`. The `listen_record` and `tts` `stage()` calls in `main.py` happen *outside* that turn and will therefore be no-ops.

To capture them, broaden the turn scope: open `profiling.turn()` in `main.py` *around* the listen → process_message → speak block, and remove the inner `profiling.turn()` from `process_message` (or leave it — a nested `turn()` should be safe, but the simpler fix is to own the scope at the top).

Pick **one** owner. Recommend: own it in `main.py` for voice mode and keep `process_message` opening a turn only for text mode (where there is no outer voice loop).

```python
with profiling.turn():
    with profiling.stage("listen_record"):
        transcription = voice.listen(...)
    # exit-word handling (no profiling needed)
    voice.play_thinking_sound()
    response = orchestrator.process_message(transcription)
    print(f"Assistant: {response}\n")
    with profiling.stage("tts"):
        voice.speak(response)
```

And in `core/orchestrator.py` change:

```python
def process_message(self, user_message: str) -> str:
    with profiling.turn():
        # ... body ...
```

to:

```python
def process_message(self, user_message: str) -> str:
    # If a turn is already open (voice mode), reuse it; else open one.
    with profiling.turn():
        # ... body ...
```

Nested `turn()` calls already work safely under the implementation in Task 1 — the inner `turn()` opens its own dict in a fresh ContextVar slot, then on exit logs an empty/duplicate summary. To avoid the duplicate summary, change the inner `turn()` to a conditional:

```python
import contextlib
...
def process_message(self, user_message: str) -> str:
    outer = profiling._current_turn.get()
    ctx = contextlib.nullcontext() if outer is not None else profiling.turn()
    with ctx:
        # ... body ...
```

This keeps the orchestrator self-contained for text mode while letting the voice loop in `main.py` own the outer scope.

- [ ] **Step 4: Run all tests to confirm no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 360 passed (5 new + existing 355).

- [ ] **Step 5: Commit**

```bash
git add main.py core/orchestrator.py
git commit -m "feat(profiling): own per-turn scope in voice loop

main.py opens profiling.turn() around the full
listen → process_message → speak window so listen_record and tts
stages land in the same [TIMING-SUMMARY] line as the inner LLM/tool
stages. process_message reuses the outer turn when present, else
opens its own (text mode)."
```

---

## Task 8: Manual validation on the Pi

**Files:** none (operational task)

- [ ] **Step 1: Push commits / rsync the touched files to the Pi**

```bash
rsync -av --relative \
  core/profiling.py \
  core/voice.py \
  core/tier_router.py \
  core/ollama_tool_loop.py \
  core/orchestrator.py \
  core/container_manager.py \
  main.py \
  tests/test_profiling.py \
  pi:/home/archimedes/miniclaw/
```

- [ ] **Step 2: Enable profiling on the Pi**

```bash
ssh pi 'echo "MINICLAW_PROFILE=true" >> ~/miniclaw/.env'
```

- [ ] **Step 3: Restart MiniClaw on the Pi (per current run procedure)**

If MiniClaw is running under systemd or a tmux session, restart it. If running manually: kill and rerun `./run.sh --voice`.

- [ ] **Step 4: Run three voice turns**

1. Direct route — e.g. *"computer, what time is it"*
2. Ollama route — e.g. *"computer, tell me a joke"*
3. Tool route — e.g. *"computer, what's the weather"*

- [ ] **Step 5: Read back the three summary lines**

```bash
ssh pi 'tail -n 200 ~/miniclaw/miniclaw.log | grep TIMING-SUMMARY'
# (or wherever logs go — check `journalctl --user -u miniclaw` if systemd)
```

Paste the three lines into the next conversation. They become the input to the follow-up brainstorm on actual fixes.

- [ ] **Step 6: Commit nothing**

This task produces measurements, not code. The next plan (latency fixes) will be written from the data.

---

## Self-Review

**Spec coverage:** every stage in the spec table is wrapped in a task (stt → Task 2; tier_route → Task 3; llm_ollama → Task 4; llm_claude + total → Task 5/7; tool_<skill> → Task 6; listen/tts/total → Task 7). Validation plan from the spec → Task 8.

**Placeholder scan:** no TBDs. Every code-changing step shows the actual code. Step 1 of Task 2 and Task 7 includes a `grep` to locate the exact line because the line numbers in `voice.py`/`main.py` may shift by the time someone executes; the substitution to make is shown explicitly in the next step.

**Type / signature consistency:** `profiling.turn()` and `profiling.stage(name)` signatures used identically across all tasks. `_current_turn` is the same ContextVar referenced in Task 7's nested-turn handling and Task 1's implementation.

**Note on TDD discipline:** Task 1 follows red-green-commit cleanly. Tasks 2–7 are observability wraps with no behavioral change — the existing test suite is the regression guard, and adding a unit test per call site would be ceremony. Task 8 is manual validation and produces the data that drives the next plan.
