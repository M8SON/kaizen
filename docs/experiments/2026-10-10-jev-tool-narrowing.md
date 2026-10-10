# Jev tool narrowing and the prompt cache (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi, real TypeSafe + Anthropic APIs, `claude-sonnet-4-6`
**Code:** branch `jev-library` (`tools:` per task category in `config/filler_phrases.yaml`,
`FillerClassifier.tool_names` at ≥ 0.8, previous turn's tools kept; weather prefetch `days: 2`)

## Question

If Claude gets only the tools of Jev's category when Jev is ≥ 0.8 confident, does routing
hold, and how much does it save?

## Method

- Routing: the 54-request archive sample; each request classified by real Jev, then sent to
  Claude with the narrowed set (≥ 0.8 task category) or all 13 tools; tools not executed.
  Baseline = all 13 tools, same sample, same code.
- Cache: identical request sent twice for weather-only, music (3 tools) and all 13 tools;
  `usage` read back. Weather voice turn captured as in earlier experiments.
- Gaps between archived requests (start-to-start) for TTL choice.
- Pricing multipliers from the claude-api skill reference: write 1.25× (5-min TTL) / 2× (1-hour),
  read 0.1×; Sonnet 4.6 minimum cacheable prefix 1,024 tokens.

## Results

Routing (54): all-tools matched archive 35, narrowed 34; same pick 52/54. Narrowed 32 requests;
same pick on 31. The one difference: "Hey Jarvis, can you play Biting List by Tal? Childers."
all-tools asked for clarification, narrowed played it on Spotify.

Replay usage (54 back-to-back requests, cache warm throughout):

| | All tools | Narrowed |
|---|---|---|
| Uncached input | 17,961 | 33,758 |
| Cache read | 125,716 | 66,939 |
| Cache write | 2,372 | 1,135 |
| Cost-equivalent (in + 0.1·read + 1.25·write) | ≈ 33,500 | ≈ 41,900 |

Cache behaviour (two identical calls each):

| Tool set | Total prompt | Call 1 | Call 2 |
|---|---|---|---|
| weather only | 1,153 | uncached 1,153, no write | uncached 1,153, no read |
| music (3 tools) | 1,458 | read 1,135 + 323 uncached | same |
| all 13 | 2,695 | read 2,372 + 323 uncached | same |

The weather-only prompt never cached (not fully explained by the 1,024 minimum at ~1,140 tokens
of prefix; observed, not diagnosed).

Weather voice turn, with 2-day prefetch: messages 567 → 281 tokens. Total 3,064 (all tools,
2,687 cacheable) vs 1,522 (weather tool only, uncached).

Request gaps (360): < 5 min 249, 5–60 min 57, > 1 hour 54.

Modelled cost-equivalent per weather turn (warm share 69% at 5-min TTL, 85% at 1-hour):
all tools 5-min ≈ 1,610; all tools 1-hour ≈ 1,420; narrowed ≈ 1,520 (never cached).

## Conclusion

Narrowing is routing-safe but saves little money: the full tool list is cheap when the cache is
warm, and narrowed prompts lose caching (weather-only never caches; per-category prefixes split
warm hits). The 2-day weather prefetch is a clear uncached saving. A 1-hour cache TTL is the
larger cost lever for this traffic (16% of gaps are 5–60 min).

## Not tested

Latency effect of fewer input tokens; real-world cost over days (modelled, not billed);
narrowing combined with a 1-hour TTL.
