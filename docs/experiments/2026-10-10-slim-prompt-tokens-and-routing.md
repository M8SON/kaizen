# Slim prompt: token usage and tool routing (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi (`ssh pi`), real Anthropic API, model `claude-sonnet-4-6`
**Code:** old = `main` at `f2906b2`+fixes (`~/kaizen`, SKILL.md bodies in the uncached system prompt);
new = branch `slim-prompt` (skill guidance only in tool definitions via `## Tool notes`),
merged to `main` the same day.

## Question

1. How many input tokens does a request cost, and what are they made of?
2. If SKILL.md bodies are dropped from the system prompt (guidance moved to short
   `## Tool notes` in the cached tool definitions), does Claude still pick the right tool?

## Method

**Tokens:** `client.messages.count_tokens` with Kaizen's real prompt pieces built by
`Orchestrator` (`build_cacheable_parts`, `get_tool_definitions`, memory recall),
adding one piece at a time to a bare user message.

**Routing:** 54 real user requests from `~/.kaizen/sessions.db`, stratified by the tool
that actually ran (8 spotify, 8 weather, 8 web-search, 6 music-control, 2 playwright-scraper,
4 soundcloud, 3 dashboard, 2 recall-session, 1 save-memory, 2 skill-tells-random, 10 no tool;
seed 7; texts 3–200 chars). Each sent once, single-turn (no conversation history), to
`messages.create(max_tokens=200)` with each checkout's system prompt and tools. Tools were
**not executed**; the first `tool_use` name (or "none") was recorded.

## Results

### Token breakdown, old prompt

| Part | "play my chill playlist" | "what is the weather tomorrow" | Cached |
|---|---|---|---|
| Tool definitions (14) | 2,446 | 2,446 | yes |
| Skill instructions (SKILL.md bodies; 3 always-full + 2 selected) | 2,501 | 1,840 | **no** |
| Persona + rules / vault memories | 381 / 82 (569 stable total) | same | yes |
| Per-turn memory recall | 83 | 83 | no |
| **Total** | **5,611** | **4,950** | |

Per-tool schema cost (includes the fixed tool-use overhead): 541–772.

### Token breakdown, new prompt

| Part | Tokens | Cached |
|---|---|---|
| Tool definitions (13; notes + 2 new schemas added, inert `update-skill-hints` dropped) | 3,023 | yes |
| Stable system | 569 | yes |
| Skill instructions | 0 | — |
| Memory recall | 83 | no |
| **Total** | **3,687** (both messages) | |

### Routing replay (54 requests)

| | Old | New |
|---|---|---|
| Uncached input tokens, all 54 | 129,232 | 17,957 (−86%) |
| Cache reads | 143,100 | 178,092 |
| Matches the archived tool | 32/54 | 34/54 |
| Old and new pick the same tool | 52/54 | |

The two old/new differences both favour new: "play some music." (old: none, new: spotify)
and the fragment "stopped." (old: music-control, new: none; archive: none).

First new run had a regression: "can you turn the volume up?" → no tool in 1 of 3 tries,
hedging "nothing is playing". Cause: the music-control note "If nothing is playing, say so".
Reworded to "Call it directly … reports if nothing is playing" → 12/12 transport requests
("volume up", "skip this song", "pause", "louder please", ×3) called music-control.

"Please remember that my favorite color is blue.": old replied "already on file" without
saving (fact present in recall); new saved it (updates the same note). 3/3 each.

Most mismatches vs the archive are replay artefacts in both versions: context-dependent
follow-ups ("Yes, please", "Yes, that's what I meant") and archive entries that predate
current skills (SoundCloud requests from before Spotify was default; forecasts that went to
web-search before the weather skill covered them, which both versions now route to weather).

### Live usage pattern (same archive)

358 user requests 2026-04-26 → 2026-10-10; 110 came ≥5 min after the previous one
(prompt cache expired → cache write). Tool calls (193): spotify 51, weather 41,
web-search 36, music-control 28, playwright-scraper 13, soundcloud 12, dashboard 6, other 8.

### Live checks after deploy (`dfe36b3`, Pi)

| When | Request | Path | Uncached in / out | Cache | Stage timings (ms) |
|---|---|---|---|---|---|
| 09:39 (script, `process_message`) | "what is the weather tomorrow?" | Claude calls weather, 2 rounds | 992 / 46 (final request) | read 3,298 | — |
| 09:40 (voice, Mason) | "Jarvis, what the weather today ?" | Jev `weather` 1.00 (291ms) → tool-first weather → 1 Claude round | 1,121 / 59 | **write** 3,339 (first request after restart) | stt 105, filler_classify 291, tool_weather 1,271, llm_claude 2,827, tts 11,338 (497ms to first audio) |

### Full request capture: voice weather turn (tool-first), before/after `aa1ddf3`

Rebuilt with the real voice path (`process_message` + Jev `intent_hint` at 1.00 + weather
prefetch, real Open-Meteo result), captured before sending, counted with `count_tokens`.

| Part | `dfe36b3` | `aa1ddf3` (recall dedupe + short hint) | Cached |
|---|---|---|---|
| Tools (13) | 3,044 | 3,044 | yes |
| Persona + 4 memories + unavailable skills | 569 | 569 | yes |
| Clock + Jev hint + memory recall | 243 | 89 (recall fully duplicated → dropped) | no |
| Messages (question, prefetch tool_use, 7-day forecast result ~450) | 567 | 567 | no |
| **Total** | **4,423** | **4,269** | |

Remaining big items: all 13 tools for an already-answered weather turn (weather alone ≈ 625),
and the full 7-day forecast for a "today" question.

For comparison, live music turns on 2026-10-10 before the change: final request 2,352 and
2,907 uncached input, cache read 2,742 (journal, 08:56 and 08:59).

## Conclusion

Dropping SKILL.md bodies cuts uncached input per request from ~1,900–2,600 to ~100 tokens
(total 4,950–5,611 → 3,687) with no loss in tool choice on real requests. Tool notes must be
worded as instructions to call, not as conditions to check first.

## Not tested

Multi-turn conversations (replay was single-turn), live voice use after deploy, the Haiku
micro tier, schedule/install-skill/set-env-var flows end to end (their protocols are in tool
notes but no request in the sample exercised them), and run-to-run variance beyond the 3×
probes above.
