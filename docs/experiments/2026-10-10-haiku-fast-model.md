# Haiku 5.5 as the fast model for Jev-confident skill requests (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi, real TypeSafe + Anthropic APIs; `claude-haiku-5-5` (effort `low`) vs
`claude-sonnet-4-6`
**Code:** branch `fast-model` (merged to `main` the same day): `fast: true` on weather, music,
web_search, memory; Jev ≥ 0.8 → `FAST_MODEL`, failures/refusals retried on the main model.

## Question

Can Haiku handle the simple skill turns (weather, music, web search, memory) that Jev is
confident about, as well as Sonnet, and faster?

## Method

1. Replay: the 54-request archive sample through real Jev; requests in a fast category at
   ≥ 0.8 sent single-turn to both models with the same system prompt and all 13 tools
   (`max_tokens=1024`, Haiku `output_config={"effort": "low"}`); tools not executed; wall time
   of `messages.create`.
2. Probe: the garbled "My jartis, can you skip? My jartis, can you skip? My j." 3× with and
   without the Jev hint ("Voice intent: music (0.89). … act on it").
3. End to end (branch checkout, `TOOL_FIRST_ENABLED=true`, tools executed): one conversation
   through `Orchestrator.process_message` — Haiku weather (prefetch), Sonnet follow-up over
   history containing the Haiku turn, Haiku "look up the latest news about SpaceX", Haiku
   "and what's the weather tomorrow then?".

## Results

Replay: 32/54 requests routed to Haiku. Same tool as Sonnet 30/32. Median wall time Sonnet
1,511ms, Haiku 1,055ms. Haiku stop reasons `end_turn`/`tool_use` only; thinking blocks present
in 17/32 responses at effort low. Differences: "continued playing music," (archived
music-control) Sonnet none / Haiku music-control; the garbled skip Sonnet music-control /
Haiku none.

Probe: without hint Haiku asked "Did you want me to skip the current song?" 3/3; with the Jev
hint it called `music-control {"action": "skip"}` 3/3.

End to end:

| Turn | Model | Time | Usage (final request) |
|---|---|---|---|
| weather today (prefetch, 2 days) | haiku | 2,451ms | 519 in / 104 out, cache_read 3,230 |
| "what did you just tell me about the rain?" | sonnet | 1,870ms | 708 in / 41 out, cache_read 2,372 |
| "look up the latest news about SpaceX" (web-search called) | haiku | 3,767ms (2 rounds) | 1,408 in / 181 out, cache_read 3,230 |
| "and what's the weather tomorrow then?" (prefetch) | haiku | 2,017ms | 1,969 in / 61 out, cache_read 3,230 |

Sonnet accepted the history containing Haiku turns; answers were correct against the fetched
data. Earlier the same weather turn on Sonnet took 3,641ms (single sample). In an earlier run
Haiku answered "who won the world series last year?" from its own knowledge without searching
(answer correct).

### Live voice check after deploy (`4d11bee`)

Mason, 11:07: "Jarvis, can you play some coffee house jazz?" → Jev music 1.00 (317ms) → Haiku
`spotify play_genre "coffee house jazz"` → music played. Timing lines (`[TIMING-SUMMARY]`, ms)
vs the two Sonnet music turns earlier that morning:

| | Sonnet 08:56 | Sonnet 08:59 | Haiku 11:07 |
|---|---|---|---|
| filler_classify | 305 | 211 | 317 |
| llm_claude (round 1) | 2,050 | 2,889 | 1,235 |
| tool_spotify | 3,408 | 3,010 | 1,992 |
| llm_claude_2 (round 2) | 1,275 | 1,482 | 769 |
| end of speech → music (classify + round 1 + tool) | ~5.8s | ~6.1s | ~3.5s |

LLM time 3.3–4.4s → 2.0s. The tool_spotify difference is Spotify API variance, not the model.
Single Haiku sample. Final request: 276 uncached in / 39 out, cache_read 3,290.

## Conclusion

Haiku matches Sonnet's tool choice on Jev-confident skill turns (30/32) and is ~30% faster per
request, at a small fraction of the price; with the Jev hint it acts on garbled transcripts.
Mixed Haiku/Sonnet history works.

## Not tested

A real refusal from Haiku (retry path covered by unit tests only); music/memory turns executed
end to end (not run, to avoid playing audio / writing memories); long sessions; whether Haiku
searches less often than Sonnet for current-events questions (one observation).
