# Jev category confidence probe (on the Pi)

**Date:** 2026-09-26 (~01:58 UTC)
**Where:** Raspberry Pi (`ssh pi`), real TypeSafe API, `typesafe-sdk>=0.7.1`
**Code:** `3963014` (run 1), then the sharpened identity criteria committed as `b5865f7` (run 2)
**Recovered from:** Claude Code transcript `61d1d7cc-f99e-48ac-b406-5e5047ae3c43` (written up 2026-10-10)

## Question

How confidently does Jev put realistic phrasings in the right category, and do
the `answer: true` categories (`identity`, `capabilities`) clear the 0.85
`FILLER_ANSWER_CONFIDENCE_THRESHOLD` without catching near-misses?

## Method

Direct `TypeSafeClient.system_one(state={"transcript": t}, questions={"category": Choice(...)})`
calls with the criteria from `config/filler_phrases.yaml` (`load_categories()`).
Typed transcripts — no Whisper/STT, no audio. One call per phrase; latency is
wall-clock from the Pi.

## Run 1 — original `identity` criteria

| Latency | Category | Confidence | Transcript |
|---|---|---|---|
| 270ms | identity | 1.0 | what is your name |
| 186ms | identity | 1.0 | who are you |
| 194ms | identity | **0.55** | what do you do |
| 170ms | capabilities | 1.0 | what can you do |
| 153ms | capabilities | 1.0 | what are you able to help me with |
| 200ms | identity | **0.51** | who am I talking to |
| 161ms | general | 0.46 | what can you do about the heat in here |
| 160ms | web_search | 0.99 | what is the name of the tallest mountain |
| 146ms | weather | 1.0 | what is the weather tomorrow |
| 182ms | music | 1.0 | play some jazz |
| 184ms | web_search | 0.99 | who is the president of france |
| 152ms | smalltalk | 1.0 | hello there |

## Run 2 — `identity` criteria with example phrasings

New criteria (now in `config/filler_phrases.yaml`): *"The user asks about the
assistant itself: its name, who or what it is, who they are talking to, or what
the assistant does or what its job is (for example "what is your name", "who are
you", "what do you do", "who am I talking to"). Not questions about any other
person or thing."*

| Latency | Category | Confidence | Transcript |
|---|---|---|---|
| 211ms | identity | 1.0 | what is your name |
| 185ms | identity | 1.0 | who are you |
| 154ms | identity | **0.98** | what do you do |
| 196ms | identity | 0.97 | what do you do exactly |
| 160ms | identity | **1.0** | who am I talking to |
| 134ms | identity | 1.0 | what is your job |
| 145ms | capabilities | 0.97 | what can you do |
| 145ms | general | 0.56 | what can you do about the heat in here |
| 187ms | web_search | 0.81 | what does a plumber do |
| 144ms | web_search | 0.99 | who is the president of france |
| 153ms | web_search | 0.98 | what is the name of the tallest mountain |
| 137ms | smalltalk | 0.94 | what do you think about pizza |
| 165ms | smalltalk | 1.0 | hello there |

## Results

- Latency 134–270ms per call, well under the 800ms `FILLER_CLASSIFIER_TIMEOUT_MS`.
- Clear-cut phrasings score 0.94–1.0. Without examples in the criteria, paraphrases
  of a category can land near 0.5 (`what do you do` 0.55, `who am I talking to` 0.51).
- Adding example phrasings to the criteria raised those to 0.98 / 1.0, with no false
  positives on near-misses: `what does a plumber do` -> web_search 0.81,
  `what can you do about the heat in here` -> general 0.56 (below the 0.85 answer bar).
- Ambiguous requests show up as low confidence (0.46–0.56) rather than a wrong
  confident answer.

## Conclusion

Jev classifies accurately when each category's criteria name concrete example
phrasings; paraphrase coverage comes from the criteria wording, not the model.
When expanding categories, add examples and re-probe borderline phrasings.

## Not tested

Real speech (Whisper/Meta transcripts, clipped or garbled input), repeated calls
for variance, and categories other than identity/capabilities beyond one phrase each.
Later live log: a clipped "to the weather today." still classified `weather` 0.92.
