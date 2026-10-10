# Jev criteria with example phrasings: archive replay before/after (on the Pi)

**Date:** 2026-10-10
**Where:** Raspberry Pi, real TypeSafe API
**Code:** old = `config/filler_phrases.yaml` at `ed2255e`; new = branch `jev-library` (generic
example phrasings added to weather, music, web_search, memory, skill_install, schedule, smalltalk;
identity/stop/capabilities/general unchanged)
**Raw data:** `/tmp/jev_eval.json` (old), `/tmp/jev_eval_new.json` (new) on the Pi

## Question

Do example phrasings in the category criteria raise Jev's confidence and accuracy on real requests,
especially music transport, without new false positives?

## Method

Same as `2026-10-10-jev-archive-replay.md`: all 361 archived user turns, direct
`system_one` call, no threshold. Ground truth = first tool that ran; a music-control turn
classified `stop` counts as correct (Kaizen's stop action stops the music).
Leakage check: 2 of the new example phrases ("skip this song", "turn it up") appear verbatim in the
archive; 4 other matches were pre-existing identity/stop examples.

## Results

| | Old | New |
|---|---|---|
| All requests ≥ 0.8 | 231/361 | 231/361 |
| Tool turns, category correct | 132/154 | 134/154 |
| Tool turns ≥ 0.8 | 119 | 125 |
| …of which correct / not | 115 / 4 | 120 / 5 |
| Median latency | 146ms | 152ms |

Per tool (correct, ≥0.8 count, median confidence), old → new: spotify 46/41/1.00 → 47/40/1.00;
weather 35/33/1.00 → 34/33/1.00; web-search 25/21/0.89 → 23/24/1.00; music-control 17/15/0.90 →
19/19/0.99; soundcloud 6/5/0.90 → 8/5/0.95.

Music transport, old → new: "Yes, please bump it up a few more times." general 0.52 → music 0.89;
"Turn the volume down." 0.85 → 1.00; "hey Jarvis can you turn the audio up?" 0.77 → 0.89;
"Can you pause the song?" music 0.70 → stop 0.98; "Hey Jarvis, resume." general 0.59 → music 0.58;
"Hey Jarba skip." smalltalk 0.76 → 0.74 (garbled).

Not correct at ≥ 0.8 (new): 3 archive errors where Jev is right (forecasts once sent to
web-search/recall), "What should I do for dinner?" → general 0.85 (all tools), and the follow-up
"Yes!." → smalltalk 0.86 (it answered a question about the music volume).

Category changes on tool turns that lost correctness all happened below 0.8
(e.g. "the news and weather today." weather 0.51 → web_search 0.66).
False `stop` on non-tool turns (≥ 0.75): 12 → 12. identity/capabilities answers (≥ 0.85): 7 → 8.

## Conclusion

Examples raise confident-and-correct tool turns 115 → 120 and fix most music-transport phrasings,
with no new false stops. Jev classifies the transcript alone, so context-dependent follow-ups
("Yes!") can be confidently mis-categorized — tool narrowing must not apply to smalltalk/general and
should keep the previous turn's tools.

## Not tested

Variance across repeated calls; categories with little traffic (schedule, skill_install, memory).
