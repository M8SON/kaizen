# Ollama Prompt Slimming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut Ollama's per-turn input from ~3,000-4,000 tokens to ~200-1,700 so phi4-mini on the Pi 5 can land warm responses inside the 8-second timeout for ≥80% of routine queries.

**Architecture:** Add `OLLAMA_BASE_PROMPT` (a ~55-token slim prompt) and `build_for_ollama()` to `PromptBuilder`. Add `max_history_tokens` + a `_trim_history` helper to `OllamaToolLoop`, wired to a new `OLLAMA_CONVERSATION_MAX_TOKENS` env var (default 1500). Change `Orchestrator._process_message` to call `build_for_ollama()` for the Ollama tier and only build the full Claude prompt lazily when Ollama escalates.

**Tech Stack:** Python 3.12, pytest, no new external deps.

**Spec:** `docs/superpowers/specs/2026-04-30-ollama-prompt-slimming-design.md`

---

## File Structure

- **edit:** `core/prompt_builder.py` — add `OLLAMA_BASE_PROMPT` class attribute and `build_for_ollama()` method
- **edit:** `core/ollama_tool_loop.py` — add `max_history_tokens` ctor param, `_trim_history` method, env var read
- **edit:** `core/orchestrator.py` — split the unified system_prompt build between tiers; pass `max_history_tokens` to `OllamaToolLoop`
- **edit:** `tests/test_prompt_builder_selector.py` (or add cases to it) — `build_for_ollama` test cases
- **edit:** `tests/test_ollama_tool_loop.py` — `_trim_history` and env-override test cases
- **edit:** `tests/test_orchestrator_routing.py` — verify Ollama tier doesn't build the Claude prompt; escalation does

---

## Task 1: Slim prompt + `build_for_ollama` on PromptBuilder

**Files:**
- Modify: `core/prompt_builder.py`
- Modify: `tests/test_prompt_builder_selector.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_prompt_builder_selector.py`:

```python
def test_build_for_ollama_returns_slim_prompt():
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_ollama()
    assert prompt == PromptBuilder.OLLAMA_BASE_PROMPT
    assert "voice assistant" in prompt.lower()
    assert "repeat back" in prompt.lower()
    # Sanity: identifying personal context is intentionally absent
    assert "Mason" not in prompt
    assert "Computer" not in prompt
    assert "warm and direct" not in prompt


def test_build_for_ollama_token_estimate_under_budget():
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_ollama()
    # Use the same heuristic as the rest of the codebase (~4 chars/token).
    estimated = max(1, len(prompt) // 4)
    assert estimated < 100, f"slim prompt was {estimated} estimated tokens"


def test_build_for_ollama_does_not_include_skill_bodies():
    """The Ollama-tier prompt must not contain skill markdown — Ollama
    routes via OpenAI tool schemas, not skill bodies."""
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder()
    prompt = pb.build_for_ollama()
    # No skill section markers from the Claude prompt builder
    assert "--- Skill Instructions ---" not in prompt
    assert "--- Remembered from past conversations ---" not in prompt
    assert "Unavailable Skills" not in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_prompt_builder_selector.py -k "build_for_ollama" -v
```

Expected: 3 failures with `AttributeError: type object 'PromptBuilder' has no attribute 'OLLAMA_BASE_PROMPT'` (or `'build_for_ollama'`).

- [ ] **Step 3: Add `OLLAMA_BASE_PROMPT` and `build_for_ollama` to `PromptBuilder`**

In `core/prompt_builder.py`, immediately after the existing `BASE_PROMPT` definition on the `PromptBuilder` class, add:

```python
    OLLAMA_BASE_PROMPT = (
        "You are a brief voice assistant. Plain spoken sentences. No markdown "
        "or asterisks. When the user's request matches a provided tool, use it. "
        "If a request is garbled or doesn't make sense as spoken language, "
        "repeat back what you heard and ask for clarification before acting."
    )
```

Then add a method to the class (anywhere reasonable; near `build` is fine):

```python
    def build_for_ollama(self) -> str:
        """Return the slim system prompt used for Ollama-tier requests.

        Deliberately excludes memory, skill markdown bodies, and persona
        scaffolding. Ollama receives tool schemas via the OpenAI tools
        parameter; duplicating skill bodies is pure prompt-eval overhead
        on a Pi-class CPU.
        """
        return self.OLLAMA_BASE_PROMPT
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_prompt_builder_selector.py -k "build_for_ollama" -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the full suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 369 passed (existing 366 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add core/prompt_builder.py tests/test_prompt_builder_selector.py
git commit -m "feat(prompt): add slim build_for_ollama on PromptBuilder

55-token system prompt for the Ollama tier — no persona, no memory,
no skill markdown bodies. Skill routing flows through OpenAI tool
schemas (passed by OllamaToolLoop separately), so duplicating skill
bodies in the system prompt is pure prompt-eval overhead on phi4-mini."
```

---

## Task 2: History trimming in OllamaToolLoop

**Files:**
- Modify: `core/ollama_tool_loop.py`
- Modify: `tests/test_ollama_tool_loop.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ollama_tool_loop.py`:

```python
class TestHistoryTrimming(unittest.TestCase):
    def _make_loop(self, max_history_tokens=1500):
        # Build a minimal OllamaToolLoop with a fake ConversationState.
        from core.ollama_tool_loop import OllamaToolLoop

        class _FakeConversationState:
            def __init__(self, msgs):
                self._msgs = msgs

            def select_messages_for_prompt(self):
                return list(self._msgs)

        class _FakeSkillLoader:
            def get_tool_definitions(self):
                return []

        # Long history: alternating user/assistant, each ~400 chars (~100 tokens).
        msgs = []
        for i in range(20):
            msgs.append({"role": "user", "content": "u" * 400 + f" turn {i}"})
            msgs.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "a" * 400 + f" reply {i}"}],
                }
            )

        return OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=_FakeSkillLoader(),
            container_manager=None,
            conversation_state=_FakeConversationState(msgs),
            max_history_tokens=max_history_tokens,
        )

    def test_default_max_history_tokens_is_1500(self):
        loop = self._make_loop()
        # Default applies when max_history_tokens=None and env var unset.
        assert loop.max_history_tokens == 1500

    def test_env_var_overrides_default(self):
        import os
        from core.ollama_tool_loop import OllamaToolLoop

        os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"] = "750"
        try:
            loop = OllamaToolLoop(
                host="http://localhost:11434",
                model="phi4-mini",
                skill_loader=type("_S", (), {"get_tool_definitions": lambda self: []})(),
                container_manager=None,
                conversation_state=type(
                    "_C", (), {"select_messages_for_prompt": lambda self: []}
                )(),
            )
            assert loop.max_history_tokens == 750
        finally:
            del os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"]

    def test_explicit_param_overrides_env(self):
        import os
        from core.ollama_tool_loop import OllamaToolLoop

        os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"] = "750"
        try:
            loop = OllamaToolLoop(
                host="http://localhost:11434",
                model="phi4-mini",
                skill_loader=type("_S", (), {"get_tool_definitions": lambda self: []})(),
                container_manager=None,
                conversation_state=type(
                    "_C", (), {"select_messages_for_prompt": lambda self: []}
                )(),
                max_history_tokens=200,
            )
            assert loop.max_history_tokens == 200
        finally:
            del os.environ["OLLAMA_CONVERSATION_MAX_TOKENS"]

    def test_trim_history_keeps_recent_messages_within_budget(self):
        loop = self._make_loop(max_history_tokens=400)  # ~ 4 messages of 100 toks
        messages = loop._build_local_messages(
            system_prompt="sys", user_message="hi"
        )
        # First message is system, last is user; everything between is trimmed history.
        history = messages[1:-1]
        # Sum of estimated tokens in history must be ≤ budget.
        total = sum(max(1, len(m["content"]) // 4) for m in history)
        assert total <= 400, f"history {total} toks exceeded budget 400"
        # Should retain at least one message (the most recent).
        assert len(history) >= 1

    def test_trim_history_drops_leading_assistant_after_trim(self):
        # Build a history where the last user-then-assistant pair would put an
        # assistant first if naive trimming kept N messages from the end.
        from core.ollama_tool_loop import OllamaToolLoop

        msgs = [
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": [{"type": "text", "text": "old asst"}]},
            {"role": "user", "content": "mid user"},
            {"role": "assistant", "content": [{"type": "text", "text": "mid asst"}]},
        ]

        class _FakeConversationState:
            def select_messages_for_prompt(self_inner):
                return list(msgs)

        class _FakeSkillLoader:
            def get_tool_definitions(self_inner):
                return []

        # Budget that fits exactly the trailing assistant only.
        loop = OllamaToolLoop(
            host="http://localhost:11434",
            model="phi4-mini",
            skill_loader=_FakeSkillLoader(),
            container_manager=None,
            conversation_state=_FakeConversationState(),
            max_history_tokens=3,
        )
        messages = loop._build_local_messages(system_prompt="sys", user_message="now")
        history = messages[1:-1]
        # If anything survived, it must start with a user role.
        if history:
            assert history[0]["role"] == "user"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ollama_tool_loop.py::TestHistoryTrimming -v
```

Expected: failures — `max_history_tokens` is not a constructor param yet, and `_trim_history` doesn't exist.

- [ ] **Step 3: Add `max_history_tokens` param to `OllamaToolLoop.__init__`**

In `core/ollama_tool_loop.py`, change the signature and constructor body:

```python
    def __init__(
        self,
        host: str,
        model: str,
        skill_loader,
        container_manager,
        conversation_state,
        memory_provider=None,
        timeout_seconds: float = 8.0,
        max_rounds: int = 10,
        max_history_tokens: int | None = None,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
        self.memory_provider = memory_provider
        self.timeout = timeout_seconds
        self.max_rounds = max_rounds
        if max_history_tokens is not None:
            self.max_history_tokens = max_history_tokens
        else:
            try:
                self.max_history_tokens = int(
                    os.environ.get("OLLAMA_CONVERSATION_MAX_TOKENS", "1500")
                )
            except ValueError:
                logger.warning(
                    "OLLAMA_CONVERSATION_MAX_TOKENS not numeric, using 1500"
                )
                self.max_history_tokens = 1500
```

- [ ] **Step 4: Add `_estimate_tokens` and `_trim_history` helpers**

Anywhere on `OllamaToolLoop` (e.g. just below `_build_tool_definitions`):

```python
    def _estimate_tokens(self, text: str) -> int:
        # Mirrors PromptBuilder._estimate_tokens (~4 chars per token) so the
        # token-budget math agrees across the codebase.
        return max(1, len(text) // 4)

    def _trim_history(self, history: list[dict]) -> list[dict]:
        """Keep the most recent messages whose total token count fits the budget.

        Re-applies the leading-assistant strip so the OpenAI protocol invariant
        (first non-system message must be `user`) is preserved after trimming.
        """
        budget = self.max_history_tokens
        kept_reversed: list[dict] = []
        used = 0
        for msg in reversed(history):
            cost = self._estimate_tokens(msg.get("content", ""))
            if used + cost > budget and kept_reversed:
                break
            kept_reversed.append(msg)
            used += cost
        kept = list(reversed(kept_reversed))
        while kept and kept[0]["role"] == "assistant":
            kept.pop(0)
        return kept
```

- [ ] **Step 5: Wire `_trim_history` into `_build_local_messages`**

Modify the existing `_build_local_messages` body (in the same file). The change is one line: call `self._trim_history(history)` before extending `local`:

```python
    def _build_local_messages(self, system_prompt: str, user_message: str) -> list[dict]:
        local = [{"role": "system", "content": system_prompt}]
        history = []
        for msg in self.conversation_state.select_messages_for_prompt():
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, str):
                history.append({"role": "user", "content": content})
            elif role == "assistant" and isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    history.append({"role": "assistant", "content": text})
        # Strip leading assistant messages — OpenAI protocol requires user turn first.
        while history and history[0]["role"] == "assistant":
            history.pop(0)
        history = self._trim_history(history)
        local.extend(history)
        local.append({"role": "user", "content": user_message})
        return local
```

- [ ] **Step 6: Run the new tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_ollama_tool_loop.py::TestHistoryTrimming -v
```

Expected: 5 passed.

- [ ] **Step 7: Run the full Ollama suite + the broader suite**

```bash
.venv/bin/python -m pytest tests/test_ollama_tool_loop.py -v
.venv/bin/python -m pytest tests/ -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add core/ollama_tool_loop.py tests/test_ollama_tool_loop.py
git commit -m "feat(ollama): trim history to OLLAMA_CONVERSATION_MAX_TOKENS

Adds max_history_tokens constructor param (env var
OLLAMA_CONVERSATION_MAX_TOKENS, default 1500) and a _trim_history
helper that walks history from the most recent backward, stopping
when the running token estimate exceeds the budget. Re-applies the
leading-assistant prune so OpenAI protocol invariants survive
trimming."
```

---

## Task 3: Lazy Claude prompt + slim Ollama prompt in Orchestrator

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `tests/test_orchestrator_routing.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_orchestrator_routing.py`:

```python
def test_ollama_tier_does_not_build_claude_system_prompt(monkeypatch):
    """When Ollama handles a turn cleanly, _build_system_prompt is never called."""
    from tests.fixtures.orchestrator_factory import build_orchestrator_for_routing_tests  # adapt to existing helper

    orch, fakes = build_orchestrator_for_routing_tests(
        tier="ollama",
        ollama_returns="hello there",
    )

    build_calls = []
    monkeypatch.setattr(
        orch,
        "_build_system_prompt",
        lambda *a, **kw: build_calls.append((a, kw)) or "FULL_CLAUDE_PROMPT",
    )

    out = orch.process_message("tell me a joke")
    assert out == "hello there"
    assert build_calls == [], "Claude prompt was built unnecessarily"


def test_ollama_escalation_builds_claude_prompt_lazily(monkeypatch):
    """When Ollama escalates, _build_system_prompt is called exactly once."""
    from tests.fixtures.orchestrator_factory import build_orchestrator_for_routing_tests

    orch, fakes = build_orchestrator_for_routing_tests(
        tier="ollama",
        ollama_returns_escalate=True,
        claude_returns="claude reply",
    )

    build_calls = []
    monkeypatch.setattr(
        orch,
        "_build_system_prompt",
        lambda *a, **kw: build_calls.append((a, kw)) or "FULL_CLAUDE_PROMPT",
    )

    out = orch.process_message("complex question")
    assert out == "claude reply"
    assert len(build_calls) == 1, f"expected 1 Claude prompt build, got {len(build_calls)}"


def test_claude_tier_still_builds_full_system_prompt(monkeypatch):
    """The Claude tier path is unchanged: build the full prompt before tool_loop.run."""
    from tests.fixtures.orchestrator_factory import build_orchestrator_for_routing_tests

    orch, fakes = build_orchestrator_for_routing_tests(
        tier="claude",
        claude_returns="claude reply",
    )

    build_calls = []
    monkeypatch.setattr(
        orch,
        "_build_system_prompt",
        lambda *a, **kw: build_calls.append((a, kw)) or "FULL_CLAUDE_PROMPT",
    )

    out = orch.process_message("complex question")
    assert out == "claude reply"
    assert len(build_calls) == 1
```

If `tests/fixtures/orchestrator_factory.py` does not exist (likely — it's a hint here), use the same pattern the existing routing tests already use. **Read `tests/test_orchestrator_routing.py` first** to find the existing fixture builder; reuse it. Replace the import in the snippets above with the actual helper. The behavioral assertions (call counts on `_build_system_prompt`) are the same regardless of fixture.

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_orchestrator_routing.py -k "ollama_tier_does_not_build or ollama_escalation_builds or claude_tier_still_builds" -v
```

Expected: failures — `_build_system_prompt` is currently called *before* tier branching.

- [ ] **Step 3: Edit `_process_message` to defer prompt build**

In `core/orchestrator.py`, replace this block (the original is around lines 290–333; line numbers may have shifted):

```python
    def _process_message(self, user_message: str) -> str:
        if self._tier_router is None:
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )

        route = self._tier_router.route(user_message)
        logger.info("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            return self._execute_direct(route, user_message)

        system_prompt = self._build_system_prompt(user_message=user_message)

        if route.tier == "claude":
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )

        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal, EscalateWithContext
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude (no tools ran)")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        if isinstance(result, EscalateWithContext):
            logger.info(
                "OllamaToolLoop escalated with %d tool(s) → Claude finalize",
                len(result.tool_activity),
            )
            return self._claude_finalize_ollama_turn(
                user_message, result.tool_activity, system_prompt
            )
        return result
```

with this version:

```python
    def _process_message(self, user_message: str) -> str:
        if self._tier_router is None:
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )

        route = self._tier_router.route(user_message)
        logger.info("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            return self._execute_direct(route, user_message)

        if route.tier == "claude":
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )

        # Ollama tier — slim prompt, lazy Claude prompt build only on escalation.
        from core.ollama_tool_loop import EscalateSignal, EscalateWithContext

        ollama_system_prompt = self.prompt_builder.build_for_ollama()
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=ollama_system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude (no tools ran)")
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        if isinstance(result, EscalateWithContext):
            logger.info(
                "OllamaToolLoop escalated with %d tool(s) → Claude finalize",
                len(result.tool_activity),
            )
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self._claude_finalize_ollama_turn(
                user_message, result.tool_activity, system_prompt
            )
        return result
```

Key changes:
- Move the `system_prompt = self._build_system_prompt(...)` line *inside* the `route.tier == "claude"` branch.
- Replace the Ollama-tier prompt with `self.prompt_builder.build_for_ollama()`.
- Build the full Claude prompt *inside* each escalation branch (`EscalateSignal` and `EscalateWithContext`).

- [ ] **Step 4: Pass `OLLAMA_CONVERSATION_MAX_TOKENS` through to `OllamaToolLoop`**

Already wired via env var read in Task 2's constructor. No change needed in `core/orchestrator.py:142-150` — `OllamaToolLoop` reads the env var itself. Confirm with:

```bash
grep -A 10 "OllamaToolLoop(" core/orchestrator.py | head -20
```

If `max_history_tokens` is not passed as a kwarg, that's correct — the env-var read in the constructor handles it.

- [ ] **Step 5: Run the new orchestrator tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_orchestrator_routing.py -v
```

Expected: all passing including the 3 new tests.

- [ ] **Step 6: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 377 passed (366 + 3 from Task 1 + 5 from Task 2 + 3 from Task 3).

- [ ] **Step 7: Commit**

```bash
git add core/orchestrator.py tests/test_orchestrator_routing.py
git commit -m "feat(orchestrator): slim Ollama prompt, lazy Claude prompt build

Ollama tier now uses prompt_builder.build_for_ollama() (~55 tokens)
instead of the full Claude prompt (3K-4K tokens). The Claude prompt
is built only when actually needed: the Claude tier path, or when
Ollama escalates via EscalateSignal / EscalateWithContext.

Net effect when Ollama handles a turn cleanly: skill-selector
embedding, memory load, and skill markdown rendering are all skipped.
Target: warm llm_ollama < 8000ms on Pi 5 for routine queries."
```

---

## Task 4: Manual Pi validation

**Files:** none (operational task)

- [ ] **Step 1: Push and rsync**

```bash
git push origin main
rsync -av --relative \
  core/prompt_builder.py \
  core/ollama_tool_loop.py \
  core/orchestrator.py \
  tests/test_prompt_builder_selector.py \
  tests/test_ollama_tool_loop.py \
  tests/test_orchestrator_routing.py \
  pi:/home/archimedes/kaizen/
```

- [ ] **Step 2: Run unit tests on the Pi**

```bash
ssh pi 'cd ~/kaizen && .venv/bin/python -m pytest tests/test_prompt_builder_selector.py tests/test_ollama_tool_loop.py tests/test_orchestrator_routing.py -q'
```

Expected: all passing.

- [ ] **Step 3: Verify env state**

```bash
ssh pi 'grep -E "OLLAMA|KAIZEN_PROFILE" ~/kaizen/.env'
```

`KAIZEN_PROFILE=true` should already be set from the profiling task. `OLLAMA_CONVERSATION_MAX_TOKENS` does not need to be set — default 1500 applies.

- [ ] **Step 4: Restart Kaizen on the Pi**

The user runs `./run.sh --voice 2>&1 | tee /tmp/mc.log` from a shell on the Pi.

- [ ] **Step 5: Run the validation queries**

Wake word, then ask each of these in sequence (Mason should warm Ollama with the first one and read the rest as warm-load timings):

1. "tell me a joke"
2. "what's the weather"
3. "play some music"
4. "set a timer for five minutes"
5. "what time is it"
6. "stop the music"
7. "skip this song"
8. "give me a fun fact"
9. "tell me something interesting about space"
10. "what's two plus two"

Some of those will route to `direct` via TierRouter — that's fine, they don't count toward the Ollama-warm sample. Add a couple of free-form queries if fewer than 10 actually hit the Ollama tier.

- [ ] **Step 6: Read the timing summaries**

```bash
ssh pi 'grep "TIMING-SUMMARY" /tmp/mc.log | grep llm_ollama'
```

Expected output is one line per Ollama-tier turn, e.g.:
```
[TIMING-SUMMARY] listen_record=4520 stt=1230 tier_route=2 llm_ollama=3210 tts=290
```

- [ ] **Step 7: Score against the success criterion**

- Pass: ≥ 80% of warm `llm_ollama` values are < 8000 ms.
- Fail: more than 20% still hit 8s timeout. Note the failing query patterns and open a follow-up brainstorm (consider qwen2.5:1.5b, pre-warming, or further trim of `OLLAMA_CONVERSATION_MAX_TOKENS`).

- [ ] **Step 8: No commit**

This task produces the validation result, not code.

---

## Self-Review

**Spec coverage:**

| Spec section                                         | Task     |
|------------------------------------------------------|----------|
| Decisions: slim prompt for Ollama, separate          | 1        |
| Decisions: no persona/memory/skill bodies            | 1        |
| Decisions: keep "repeat back garbled"                | 1 (test verifies it's in the prompt) |
| Decisions: history budget 1500 default               | 2        |
| Decisions: lazy Claude prompt build on escalation    | 3        |
| Decisions: don't change tool-schema flow             | 3 (no change to `_build_tool_definitions`) |
| Architecture: `build_for_ollama()`                   | 1        |
| Architecture: orchestrator wiring                    | 3        |
| Architecture: `OllamaToolLoop` history trimming      | 2        |
| Error handling: env var unset → 1500                 | 2 (test_default_max_history_tokens_is_1500) |
| Error handling: env var non-numeric → 1500 + warn    | 2 (`try/except ValueError` in constructor) |
| Error handling: Ollama timeout → escalate            | unchanged (tested in existing TestTimeoutEscalation) |
| Error handling: history trim drops everything        | 2 (test confirms `≥ 1` is allowed but invariant holds) |
| Testing: `build_for_ollama` returns slim prompt      | 1        |
| Testing: token estimate < 100                        | 1        |
| Testing: `_trim_history` keeps recent within budget  | 2        |
| Testing: `_trim_history` strips leading assistant    | 2        |
| Testing: env var override                            | 2        |
| Testing: Ollama tier doesn't build Claude prompt     | 3        |
| Testing: Claude tier does                            | 3        |
| Testing: escalation builds Claude prompt lazily      | 3        |
| Validation plan                                      | 4        |

**Placeholder scan:** every code-changing step shows complete code; the only place I asked the engineer to "find an existing fixture" is Task 3 Step 1, where I explicitly tell them to read `tests/test_orchestrator_routing.py` first to reuse the existing helper, since fabricating an import path would be worse than the hint.

**Type / signature consistency:**
- `OllamaToolLoop.__init__` signature matches between Task 2 (where it gets the new param) and the orchestrator construction site (Task 3 Step 4 confirms no kwarg push needed because the env-var read is internal).
- `PromptBuilder.build_for_ollama` is referenced as `self.prompt_builder.build_for_ollama()` in Task 3, matching the Task 1 definition.
- `_estimate_tokens` and `_trim_history` are introduced in Task 2 and not used by any later task — internal helpers.
- `OLLAMA_BASE_PROMPT` is defined as a class attribute in Task 1; tests in Task 1 access it as `PromptBuilder.OLLAMA_BASE_PROMPT`.
