# Ollama Prompt Slimming

**Status:** Spec
**Date:** 2026-04-30
**Owner:** Mason Misch

## Problem

Live testing on the Pi (2026-04-30) showed every Ollama call timing out at
the 8s `OLLAMA_TIMEOUT` and escalating to Claude. Root cause: the
orchestrator builds one Claude-grade system prompt with `_build_system_prompt()`
and hands the same blob to both tiers. The Claude prompt is 3,000–4,000 tokens
of skill markdown, memory vault notes, persona, and self-update guidance. On
phi4-mini running on Pi 5 CPU, prompt-evaluation at that size pushes
time-to-first-token past 8s before any output is generated.

Skill markdown is also pure overhead for Ollama: tool schemas are already
passed via the OpenAI-format `tools` parameter, so the bodies are duplicated
information.

## Goal

Hit `[TIMING-SUMMARY] llm_ollama < 8000` ms for ≥80% of warm Ollama calls on
the Pi, measured by the profiling that shipped 2026-04-30. Cold-load first
call after Ollama unloads is allowed to exceed that.

## Non-Goals

- Slim Claude's system prompt. Deferred to a separate brainstorm.
- Switch to a smaller Ollama model (e.g. qwen2.5:1.5b). Only revisit if
  prompt-slimming alone doesn't hit the target.
- Pre-warm Ollama at startup.
- Stream Ollama tokens to Kokoro TTS.

## Decisions

- **Slim prompt for Ollama, separate from Claude.** Hand-written, ~55 tokens.
- **No persona, no name, no memory, no skill markdown bodies, no
  date/weather injection** in the Ollama prompt.
- **Keep the "repeat back garbled requests" guidance** so Ollama still
  handles transcription errors gracefully.
- **History budget for Ollama: env-tunable via `OLLAMA_CONVERSATION_MAX_TOKENS`,
  default `1500`** (≈2–3 turns).
- **Lazy Claude prompt build on escalation:** when Ollama handles a query
  without escalating, the full Claude prompt is never built — a meaningful
  bonus speed win on top of the prompt-slim itself.
- **Don't change `OllamaToolLoop`'s tool-schema flow** — `_build_tool_definitions()`
  already produces a slim OpenAI-format list from `skill_loader.get_tool_definitions()`.

## The Slim Ollama System Prompt

A new class attribute on `PromptBuilder` (sibling to the existing `BASE_PROMPT`):

```python
OLLAMA_BASE_PROMPT = (
    "You are a brief voice assistant. Plain spoken sentences. No markdown "
    "or asterisks. When the user's request matches a provided tool, use it. "
    "If a request is garbled or doesn't make sense as spoken language, "
    "repeat back what you heard and ask for clarification before acting."
)
```

Approximate token count: 55. `PromptBuilder.build_for_ollama()` returns this
constant verbatim. No memory, no skill bodies, no skipped-skills section, no
self-update guidance. Tool schemas continue to flow via the OpenAI `tools`
parameter.

## Architecture

### `PromptBuilder.build_for_ollama()`

```python
def build_for_ollama(self) -> str:
    """Return the slim system prompt used for Ollama-tier requests.

    Deliberately does not include memory, skill bodies, or any of the
    Claude-only context — phi4-mini on Pi 5 CPU is prompt-eval-bound, and
    Ollama already receives tool schemas via the OpenAI tools parameter.
    """
    return self.OLLAMA_BASE_PROMPT
```

No arguments. Pure constant return. Tested for token count and exact text
identity.

### Orchestrator wiring

`core/orchestrator.py` `_process_message` changes:

```python
# Before:
system_prompt = self._build_system_prompt(user_message=user_message)
if route.tier == "claude":
    return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt, ...)
result = self._ollama_tool_loop.run(user_message=user_message, system_prompt=system_prompt)

# After:
if route.tier == "claude":
    system_prompt = self._build_system_prompt(user_message=user_message)
    return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt, ...)

ollama_system_prompt = self.prompt_builder.build_for_ollama()
result = self._ollama_tool_loop.run(
    user_message=user_message, system_prompt=ollama_system_prompt
)
if result is EscalateSignal:
    # Build the full Claude prompt only when actually escalating.
    system_prompt = self._build_system_prompt(user_message=user_message)
    return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
if isinstance(result, EscalateWithContext):
    system_prompt = self._build_system_prompt(user_message=user_message)
    return self._claude_finalize_ollama_turn(user_message, result.tool_activity, system_prompt)
```

`_claude_finalize_ollama_turn` already takes `system_prompt` as a parameter,
so the lazy-build threads through cleanly.

### `OllamaToolLoop` history trimming

New constructor parameter and env var:

```python
def __init__(self, ..., max_history_tokens: int | None = None):
    ...
    self.max_history_tokens = (
        max_history_tokens
        if max_history_tokens is not None
        else int(os.environ.get("OLLAMA_CONVERSATION_MAX_TOKENS", "1500"))
    )
```

`_build_local_messages` is updated to enforce the budget:

```python
def _build_local_messages(self, system_prompt: str, user_message: str) -> list[dict]:
    local = [{"role": "system", "content": system_prompt}]
    history = []
    for msg in self.conversation_state.select_messages_for_prompt():
        # ... existing role/content extraction ...
        history.append(...)
    while history and history[0]["role"] == "assistant":
        history.pop(0)
    history = self._trim_history(history)
    local.extend(history)
    local.append({"role": "user", "content": user_message})
    return local

def _trim_history(self, history: list[dict]) -> list[dict]:
    """Keep the most recent messages whose total token count fits the budget."""
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

def _estimate_tokens(self, text: str) -> int:
    # Reuse PromptBuilder._estimate_tokens (~4 chars per token) so all
    # token-budget logic agrees. Either import-and-call or copy the
    # one-line implementation; do not invent a third heuristic.
    return max(1, len(text) // 4)
```

Re-applies the "leading assistant message" pruning after trimming so the
OpenAI protocol invariant (first non-system message must be `user`) is
preserved.

## Data Flow

```
TierRouter.route(transcript) → tier=ollama
Orchestrator._process_message:
    ollama_system_prompt = prompt_builder.build_for_ollama()  # ~55 tokens
    OllamaToolLoop.run(
        user_message=transcript,
        system_prompt=ollama_system_prompt,
    )
        _build_local_messages:
            messages = [system, ...trimmed history (≤1500 tokens), user]
        _build_tool_definitions:
            tools = [...openai-format schemas, name+desc+params only...]
        POST /v1/chat/completions  ← total prompt ~ 200–1700 tokens
```

When Ollama returns content cleanly, the response is committed to
`ConversationState` and returned. The full Claude prompt (3K–4K tokens) is
never built for that turn.

When Ollama escalates, the orchestrator builds the Claude prompt lazily and
runs `tool_loop.run` with full context — same as today, just deferred.

## Error Handling

| Path                                            | Behavior                                                                  |
|-------------------------------------------------|---------------------------------------------------------------------------|
| `OLLAMA_CONVERSATION_MAX_TOKENS` unset          | Default 1500 used                                                         |
| `OLLAMA_CONVERSATION_MAX_TOKENS` non-numeric    | Falls back to 1500 with a startup warning                                 |
| Ollama timeout / network error                  | Escalate to Claude (existing behavior unchanged)                          |
| Ollama returns empty content                    | Escalate to Claude (existing)                                             |
| History trim drops everything                   | Empty history; system + user only — Ollama still attempts the turn        |

## Testing

| Test                                                  | File                                                |
|-------------------------------------------------------|-----------------------------------------------------|
| `build_for_ollama` returns the slim prompt verbatim   | `tests/test_prompt_builder.py` (existing or new)    |
| `build_for_ollama` is < 100 tokens (estimator)        | same                                                |
| `_trim_history` keeps recent messages within budget   | `tests/test_ollama_tool_loop.py`                    |
| `_trim_history` strips leading assistant after trim   | same                                                |
| `OLLAMA_CONVERSATION_MAX_TOKENS` env override applied | same                                                |
| Ollama tier does NOT call `_build_system_prompt`      | `tests/test_orchestrator_routing.py`                |
| Claude tier DOES call `_build_system_prompt`          | same                                                |
| `EscalateSignal` causes lazy Claude prompt build      | same                                                |
| `EscalateWithContext` causes lazy Claude prompt build | same                                                |

No new integration test on CI — the latency target is validated manually on
the Pi.

## Validation

1. Pull this on the Pi (`git pull && rsync` is fine since we deploy via rsync).
2. Restart MiniClaw with `MINICLAW_PROFILE=true` already set.
3. Run 10 routine voice queries Ollama should handle:
   - "tell me a joke"
   - "what's the weather"
   - "play some music"
   - "set a timer for five minutes"
   - "what time is it"
   - "stop the music"
   - "skip this song"
   - "give me a fun fact"
   - "tell me something interesting about space"
   - "what's two plus two"
4. Grep `[TIMING-SUMMARY]` from the log; collect `llm_ollama=N` per turn.
5. Pass: 8/10 (or better) of the warm calls land below 8000 ms.
6. Fail → consider further prompt-slimming or smaller model in a follow-up.

## Out of Scope

- Pre-warming Ollama with a dummy call at startup.
- Streaming Ollama tokens to TTS.
- Switching from phi4-mini to qwen2.5:1.5b or smaller.
- Slimming Claude's system prompt (separate brainstorm).
- Adjusting `OLLAMA_TIMEOUT` from 8s.
- Adjusting `temperature` (Ollama config concern, not prompt-building).
