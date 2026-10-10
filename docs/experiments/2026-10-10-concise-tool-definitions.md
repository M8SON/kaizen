# Concise tool definitions: tokens and routing (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi (`ssh pi`), real Anthropic API, `claude-sonnet-4-6`
**Code:** old = live `main` `aa1ddf3`; new = branch `concise-tools` (12 SKILL.md files: one-sentence
`description`, `## Tool notes` only where they add a rule, terse schemas; SoundCloud schema reduced
to `query`), merged to `main` the same day.

## Question

Can the tool definitions (69% of a request) be much shorter without changing which tool Claude picks?

## Method

- Tokens: `count_tokens` per tool, alone and with its schema emptied; fixed overhead measured with a
  one-word dummy tool. Weather request rebuilt and captured as in
  `2026-10-10-slim-prompt-tokens-and-routing.md`.
- Routing: 54 archived requests (rebuilt sample, same method/seed 7; archive had grown, so not the
  identical 54 as the earlier run), single-turn, tools not executed; plus 3–5× probes of every
  old/new difference.

## Results

### Tokens

| | Old | New |
|---|---|---|
| All 13 tool definitions | 3,044 | 2,089 |
| of which fixed tool-use overhead | ~530 | ~530 |
| tool content (sum of per-tool marginals) | 2,118 | 1,163 |
| descriptions / schemas | 1,004 / 1,114 | 501 / 662 |
| Weather voice request, total | 4,269 | 3,314 |

Largest per tool, new: dashboard 217, schedule 201, spotify 135 (old: spotify 328, schedule 285,
dashboard 284).

### Routing, 54 requests

| | Old | New (final) |
|---|---|---|
| Matches archived tool | 35/54 | 35/54 |
| Old and new pick the same tool | 49/54 | |

Regressions found by the replay and fixed before merge:
- "Can you play EDM music for me? I just asked you that." → recall-session (new recall wording
  "what was said before" matched "I just asked you that"). Reworded to "earlier sessions … not this
  conversation" → spotify 3/3.
- "play some music." → asked "What are you in the mood for?" 1/6. Added to the spotify note "for a
  vague request like 'play some music', pick one rather than asking" → 15/15 spotify ("play some
  music.", "put on some music", "play something", ×5).

Remaining differences, probed:
- "Please remember that my favorite color is blue." New: "Already got that one saved" (the fact is
  in saved memories), saved 1/3; old saved 3/3. New facts ("my dog is called Pip", "I prefer
  celsius", "my sister lives in Denver") → save-memory 9/9.
- "Hey Jarvis, can you play Biting List by Tal? Childers." Old: spotify 5/5 silently. New: "I think
  you mean Tyler Childers. Let me pull that up." + spotify 4/5 (5th line truncated in the probe output).
- "The song is called Out of My Link by Fits and the Tantrums." Both ask whether it's "Out of My
  League" by Fitz and the Tantrums (3/3 each).
- "is this coffeehouse jazz…" old web-search, new no tool; "<|en|><|transcribe|>… Darth Vader Joe"
  garbled, spotify vs soundcloud (both wrong).
- "What did we talk about yesterday about the dashboard?" recall-session 3/3 in both.

## Conclusion

Tool content roughly halved (−955 tokens per request, all cached) with routing unchanged on real
requests. Two wording regressions were caught only by the replay; tool notes must not contain phrases
that match everyday speech ("what was said before") and should tell Claude to act on vague requests.

## Not tested

Live multi-turn use, the Haiku micro tier, schedule/set-env-var/install-skill confirmation flows
end to end, dashboard requests (no monitor connected).
