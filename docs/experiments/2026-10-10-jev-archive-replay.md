# Jev on every archived request (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi (`ssh pi`), real TypeSafe API, categories from `config/filler_phrases.yaml`
at `aa1ddf3` (no example phrasings except `identity` and `stop`)
**Raw data:** `/tmp/jev_eval.json` on the Pi (361 rows: text, tool that ran, category, confidence, ms)

## Question

How confident and how accurate is Jev on real requests? Is it safe to give Claude only the
tools of Jev's category when Jev is confident (step B of the token work)?

## Method

All 361 user turns in `~/.kaizen/sessions.db` (2026-04-26 → 2026-10-10), real STT transcripts
as archived. Each sent once to `client.system_one(state={"transcript": t},
questions={"category": Choice(criteria=load_categories())})` — called directly, so no
confidence threshold is applied. Ground truth = the first tool that ran in that turn
(spotify/soundcloud/music-control → music, weather → weather, web-search/playwright-scraper →
web_search, save-memory/recall-session → memory). Archived routing reflects the skills and
prompts of its day, so it is imperfect ground truth.

## Results

Latency: median 146ms, max 315ms.

| Confidence | Requests |
|---|---|
| 0.95–1.00 | 136 |
| 0.90–0.95 | 46 |
| 0.80–0.90 | 49 |
| 0.60–0.80 | 52 |
| < 0.60 | 78 |

154 turns ran a tool with a mapped category; Jev matched 129.

| Threshold | Turns at/above | Match | Mismatch |
|---|---|---|---|
| ≥ 0.6 | 136 | 121 | 15 |
| ≥ 0.8 | 119 (77%) | 112 | 7 |
| ≥ 0.9 | 104 | 99 | 5 |

Per tool (n, match, median confidence): spotify 51/46/1.00, weather 39/35/1.00,
web-search 29/25/0.89, music-control 22/14/0.90, soundcloud 8/6/0.90, others ≤2 each.

The 7 mismatches at ≥ 0.8:
- Jev right, archive wrong (old routing sent forecasts to web-search): "What is the weather
  for tomorrow?", "When is going to be the next, nice day. in Burlington, Vermont.",
  "What is the weather like today?" (archived recall-session) → weather 1.00.
- `stop` instead of music-control, which Kaizen handles by stopping music: "Pause." 0.89,
  "can you stop the music?" 0.90, "hey Jarvis can you stop the EDM now?" 0.82.
- "What should I do for dinner?" → general 0.91 (archived: weather) — general keeps all tools.

Music transport is the weak spot (no transport examples in the `music` criteria):
"Hey Jarba skip." smalltalk 0.76, "Hey Jarvis, resume." general 0.59,
"Yes, please bump it up a few more times." general 0.52, "Yes, keep up" general 0.70.

Low-confidence requests are mostly not real requests: background speech, context-dependent
follow-ups ("Yes, that's what I meant." 0.54), a garbled transcript
("<|en|><|transcribe|>… Darth Vader Joe" 0.38).

Non-tool turns (207): general 76, smalltalk 40, music 27, stop 13, identity 11,
web_search 11, memory 11, weather 7; 39 of them ≥ 0.8 in a tool category (follow-ups and
chatter; narrowing them would only remove tools Claude didn't use).

## Conclusion

Jev is confident on real requests (231/361 ≥ 0.8) and, at ≥ 0.8, no confident
classification would have removed a tool the request needed: every mismatch was an archive
error, a `stop` equivalent, or `general` (all tools). Narrowing tools by Jev category at
≥ 0.8 is safe on this data; music transport phrasings need examples in the criteria.

## Not tested

Multi-turn context (follow-ups were classified alone), variance across repeated calls, and
categories with almost no archived traffic (schedule, skill_install, memory: ≤2 each).
