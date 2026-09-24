# Research: filler-phrase classifier for the STT→answer gap

**Date:** 2026-09-23
**Status:** Research only — no code/design changes recommended here.
**Question:** Should Kaizen add a local classifier (candidate: "Laya", ~421M
params, ~200–500ms CPU inference) after STT to pick a topic-specific canned
filler phrase, spoken while the real Claude/tool-calling turn (2–5s+) is in
flight, instead of the current generic R2-D2 beep?

Kaizen's own docs already establish the relevant baseline numbers, cited
inline below alongside external sources.

---

## 1. Prior art: how do commercial/research voice assistants fill the gap?

**Commercial assistants mostly use non-specific, cheap techniques — not a
full classifier:**
- The dominant pattern across Alexa/Siri/Google-style assistants and current
  voice-AI platforms (Vapi, Synthflow, Dialzara) is a short generic
  acknowledgement — "Let me check", "One moment", "Got it" — played
  **immediately on end-of-speech, before any NLU/LLM work begins**. This
  requires no classification at all — it's a fixed pool of neutral phrases,
  sometimes randomly rotated. (docs.vapi.ai/prompting-guide;
  dialzara.com/blog/best-ai-voice-receptionist-prompts;
  synthflow.ai/blog/conversational-ai-ivr)
- A practitioner write-up ("Your Voice Agent Is Slow. Here Are 5 Tricks to
  Hide It", dev.to/kenimo49) lays out a five-tier taxonomy: (1) instant
  generic acknowledgment tokens (no LLM, no classifier — picked by rough
  intent heuristic at most), (2) disfluency-style fillers ("um", "let me
  think") for 4s+ waits, (3) progressive disclosure of partial answers, (4)
  **intent-tied "warmup phrases"** ("Checking the weather now") which *do*
  require classification, and (5) visual decoys for multimodal UIs. It
  explicitly frames (4) as needing "solid intent classification" and warns
  that a wrong warmup ("Checking your schedule" → "actually I can't help
  with that") is worse than a generic one.
  https://dev.to/kenimo49/your-voice-agent-is-slow-here-are-5-tricks-to-hide-it-3pcb
- A voice-AI ops blog (mindlyticai.com) reaches the same conclusion from
  production experience: fillers are injected "as soon as the user's turn
  ends, in parallel with the LLM call" — i.e., filler selection does not wait
  on classification; it's near-instant and mostly generic, with turn-taking
  and barge-in classifiers treated as a *separate*, much more latency-
  sensitive problem (they specifically moved that classifier on-device
  because a cloud round trip added "80ms of latency that mattered").
  https://mindlyticai.com/blog/voice-ai-latency

**Do any of them use a classifier for a *topic-specific* filler vs. a single
generic one?** Not among mainstream commercial assistants, as far as public
documentation shows — Alexa/Siri/Google Assistant behavior here is
proprietary and not documented at this level of detail; the pattern
observable in public voice-AI platform guidance is fixed-set generic
fillers. The topic-specific approach appears mainly in:
- **Research**: "Thinking While Speaking" / **ConvFill** (arXiv 2511.07397,
  2026) formalizes a "Talker-Reasoner" split: a small on-device language
  model (135M–1.7B params) generates *contextually grounded* filler
  responses while a frontier "Reasoner" (e.g. Claude/GPT-4) does the slow
  tool-calling/reasoning. This is the closest published analogue to Kaizen's
  proposed classifier+phrase-library design, except ConvFill uses a
  generative SLM rather than a classifier-over-a-fixed-library, and reports
  concrete numbers (see §3).
  https://alphaxiv.org/abs/2511.07397 / https://doi.org/10.48550/arxiv.2511.07397
- **HCI research on filler *content*, not selection mechanism**: a VR-based
  study (ACM ICMI/CUI 2025, "Please Let Me Think") found that **verbal
  fillers reduce perceived waiting time** regardless of anthropomorphism, and
  that phrasing quality matters more than topic-specificity — "Let me think
  for a moment" and "One moment please" rated best; generic-but-natural beat
  robotic ("Give me a moment to generate your response"). This supports
  investing effort in phrase *naturalness*, not necessarily in
  topic-accurate selection.
  https://dl.acm.org/doi/full/10.1145/3716553.3750792
- Classic latency-perception thresholds cited in that same paper (Card et
  al.): <0.1s = imperceptible, ~1s = acceptable, ~10s = unacceptable/
  frustrating — useful as a sanity check for where Kaizen's own 2–5s+
  tool-calling turns sit (solidly "unacceptable without feedback" territory).

**Takeaway for §1:** Generic, classifier-free acknowledgements are standard
practice and already sufficient to fix the dead-air problem. Topic-specific
fillers are an active research direction (ConvFill) with real UX upside
reported, but no evidence any shipped commercial assistant does classifier
→ fixed-phrase-library selection the way the proposal describes; the closest
prior art either (a) skips classification and just uses a generic phrase, or
(b) replaces classification entirely with a small generative talker model.

---

## 2. Is ElevenLabs Flash v2.5 fast enough that a 30–500ms classifier still
lands well before the real answer?

**Kaizen's own docs (`2026-07-23-elevenlabs-tts-backend-design.md`,
`CLAUDE.md`) already record:** Flash v2.5 selected specifically for its
~75ms *model inference* time, expected to synthesize+begin playback well
under Kokoro's local first-audio floor (1.5–6s on Pi 5 CPU, per
`2026-07-18-first-answer-latency-warmup-design.md`).

**External benchmarks refine what "~75ms" actually means end-to-end:**
- ElevenLabs' own docs are explicit that 75ms is **model inference only**,
  excluding network round-trip and app overhead, and that real
  Time-To-First-Audio (TTFA) "is almost always the number that matters... and
  is always larger, often substantially larger." Their own latency-
  optimization guide gives **WebSocket-based Flash TTFB by region**: North
  America / Europe / SE Asia 100–150ms; South/NE Asia 150–200ms.
  https://elevenlabs.io/docs/eleven-api/concepts/latency.mdx
  https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization.mdx
- Independent benchmark aggregator (Coval) measures Flash v2.5 in production
  traffic at **TTFA p50 ≈ 201ms, p50 network round-trip ≈ 158ms, p99 spikes
  to ~3.4s** (tail latency under load/queueing is real and non-trivial).
  https://benchmarks.coval.ai/models/eleven_flash_v2_5
- A residential/high-latency-region real-world test (India, non-US) measured
  **~478–500ms TTFB** using best-case streaming REST/PCM, dominated by TCP/TLS
  handshake to US servers (~375ms) — i.e., from a Pi on a home ISP connection
  *without* a nearby regional endpoint, total wait to first audio can land in
  the 400–600ms range in the worst geographic case, not 75ms.
  https://vexyl.ai/elevenlabs-tts-latency-test-2026-real-world-results/

**Answer to the specific question:** Yes — even at the pessimistic end
(≈500ms TTFA from a residential/non-US-region client) plus a 500ms
worst-case classifier call, total time to filler-speech-start is roughly
0.5–1.0s. That is still comfortably inside Kaizen's own measured 2–5s+
(often 13–16s for weather/web-search per `first-answer-latency-warmup-
design.md`) tool-calling turn latency. On typical/US-region hardware
(Flash p50 ≈ 150–200ms TTFA) plus a fast classifier (~30–100ms, as would be
expected on a Pi 5 CPU or offloaded to the Hailo-8L NPU already in Kaizen's
target hardware), the filler would begin well under 300ms after STT
completes — i.e., landing inside the "acceptable, non-disruptive" latency
band from Card et al., and finishing well before the real Claude answer in
essentially every case, including simple non-tool turns (Kaizen's own
measured cold LLM leg is ~1.5–3.1s even without tool calls). **The
classifier's 30–500ms budget is not the bottleneck; network/TTFA variance at
the tail (p99 ~3.4s per Coval) is a bigger risk than the classifier step
itself**, and that risk is intrinsic to the ElevenLabs choice already made,
independent of adding a classifier.

**One caveat worth flagging (not a code recommendation, just an observation
for the eventual design discussion):** Kaizen's ElevenLabs backend design
doc notes Flash's **75ms figure is for the model, and API self-check +
connection setup already happen at startup, not per-turn** — so the
per-turn cost that matters is the streaming TTFA numbers above, not the
startup self-check. Also worth noting: if the classifier runs to select
*which* filler phrase to speak, and that phrase then goes through the same
ElevenLabs Flash pipeline (rather than being pre-synthesized/cached), the
classifier delay and TTFA delay are additive in sequence, not parallel —
they should be summed (as done above), not compared independently.

---

## 3. Existing open-source patterns, and how bad a misclassification is

**Directly on-point prior art (closest pattern match):**
- **ConvFill / "Thinking While Speaking"** (arXiv 2511.07397) is the most
  rigorous public precedent for exactly this architecture. Reported numbers
  from their live user study (Apple M2 SoC, n=18): Talker filler
  Time-to-First-Response of **542ms (direct queries)** vs Reasoner 2,947ms;
  **976ms (RAG)** vs Reasoner 4,852ms; tool-use/MCP gap even larger. They
  found fillers close the perceived-responsiveness gap dramatically and
  users significantly preferred the system for retrieval-heavy tasks, but
  flagged a **"Naturalness" penalty** — users found repeated/predictable
  filler phrases slightly artificial, even when the fillers were contextually
  correct. This is a caution about phrase-library staleness/repetition, not
  about misclassification specifically, but implies **variety matters as
  much as topical accuracy**.
  https://alphaxiv.org/abs/2511.07397
- **Speculative-response / speculative-TTS pattern**, more broadly: several
  small open-source projects implement the general idea of doing useful work
  during the STT-finalization gap:
  - NVIDIA's voice-agent-examples reference doc on "Speculative Speech
    Processing" describes generating responses from *interim* (pre-final)
    ASR transcripts to cut perceived latency — a related but distinct
    technique (speculating on the user's *words*, not selecting a filler for
    a *known* final transcript).
    https://github.com/NVIDIA/voice-agent-examples/blob/main/docs/SPECULATIVE_SPEECH_PROCESSING.md
  - Smaller community projects (e.g. a GitHub repo named "precog" and one
    named "specvoice") describe speculative pre-generation and TTS-phrase
    caching for phone agents, including a `confidence_threshold` gate before
    committing to a speculative branch, and a pre-warmed cache of a handful
    of very generic phrases ("Sure, let me check that for you", "One moment
    please") rather than large topic-specific libraries. These are small
    (single-digit-star) hobby/community projects, not established or
    widely-adopted references — treat as illustrative of the pattern, not as
    validated production evidence.

**Accuracy/precision bar and graceful degradation guidance:**
There is no published, quantified "X% accuracy required" threshold for this
specific filler-classifier use case — it hasn't been studied as a discrete
metric anywhere found in this research pass. What the literature and
practitioner guidance converge on instead is a **qualitative design
principle**: keep the filler content *hedged/vague enough that being wrong
about topic doesn't read as wrong*, and treat definitive topic-specific
claims as risky:
- The clearest explicit warning: "*The crucial difference [with intent-tied
  warmup phrases] is that this is context-aware... Where it backfires: When
  intent classification is wrong. 'Checking your schedule' followed by
  'actually I can't help with that' is worse than just admitting it from the
  start.*" (dev.to/kenimo49). This directly answers the "how bad is it"
  question: a **confidently wrong, specific filler is worse than a generic
  one** — it creates a moment of dissonance/false-start that a neutral filler
  never risks. The practical mitigation implied is a **confidence threshold**
  gating specific phrasing, falling back to generic phrasing when the
  classifier is unsure (mirrors "precog"'s `confidence_threshold: 0.7`
  design and NLU literature's "disambiguation" pattern for low-confidence
  intents, e.g. UXmatters' voice-UI guidance on asking a clarifying follow-up
  rather than guessing:
  https://www.uxmatters.com/mt/archives/2018/01/designing-voice-user-interfaces.php).
- The VR filler-perception study found phrasing that avoids specific claims
  ("Let me think for a moment") rated as highly as more specific phrasing,
  and non-verbal/vague fillers were *not* penalized relative to verbal ones
  for correctness — reinforcing that **vagueness is a safe default**, and
  specificity is a bonus, not a requirement, for the perceived-latency
  benefit to land. https://dl.acm.org/doi/full/10.1145/3716553.3750792
- General VUI "design for failure" guidance (Smashing Magazine, UXmatters)
  applies by extension: any spoken system output that could be objectively
  wrong needs a low-cost recovery path; a misclassified filler is a minor
  instance of this because it's immediately followed (1–3s later) by the
  correct real answer, which self-corrects the record — unlike a
  misclassified *final* answer, a wrong filler is transient and low-stakes
  as long as it doesn't make a factual claim ("it's sunny today") rather than
  a neutral intent statement ("let me check the weather").
  https://uxplanet.org/16-rules-of-effective-ux-writing-2a20cf85fdbf (general
  writing-clarity backdrop, not filler-specific)

**Practical implication (not a recommendation, an observation):** the
literature suggests the risk profile is asymmetric — a wrong *generic*
filler costs nothing; a wrong *topic-specific* filler costs a brief
dissonance beat, self-corrected within ~1–5s when the real answer arrives.
None of the sources found suggest topic-specific filler misclassification
causes lasting UX damage (no source frames it as more than a momentary
"huh?" beat) — but all of them converge on hedged phrasing and/or a
confidence gate as how production systems avoid even that.

---

## Summary / answer to "is this category worth building at all"

- Latency math is favorable: even pessimistic ElevenLabs TTFA (~500ms,
  residential/non-US) plus worst-case classifier (500ms) lands around 1s —
  well inside Kaizen's own measured 2–5s+ (often much higher for
  tool-calling) real-answer latency. The classifier step is not the
  bottleneck; it would not meaningfully erode the existing latency-hiding
  win that ElevenLabs Flash v2.5 already provides.
- The strongest, most directly comparable published evidence (ConvFill,
  arXiv 2511.07397) shows topic-grounded fillers materially improve
  perceived responsiveness and user preference for exactly Kaizen's
  hardest case (tool-calling/retrieval turns), at some (studied, non-fatal)
  cost to "naturalness" from repetition.
  What commercial/production voice-AI practice mostly does instead is
  simpler and cheaper: skip the classifier, use a small pool of generic,
  hedged acknowledgements. That gets most of the latency-hiding benefit with
  none of the misclassification risk and none of the ~30–500ms/extra-
  dependency cost.
  The net-positive case for a *topic-specific classifier specifically* (vs.
  generic fillers) rests on whether topic-specificity itself is worth the
  added complexity — the evidence for that incremental benefit (beyond what
  a generic "let me check" already buys) is thinner and comes from a single
  recent research system (ConvFill) rather than converged industry practice.
  If pursued, the accuracy bar implied by the literature is: high-confidence
  predictions get specific phrasing, low-confidence predictions fall back to
  hedged/generic phrasing — not a fixed accuracy percentage, but a
  confidence-gated two-tier phrase strategy.

---

## Sources

1. ConvFill / "Thinking While Speaking" — arXiv 2511.07397 (2026):
   https://alphaxiv.org/abs/2511.07397 · https://doi.org/10.48550/arxiv.2511.07397
2. "Please Let Me Think" — ACM ICMI/CUI 2025, conversational fillers in VR:
   https://dl.acm.org/doi/full/10.1145/3716553.3750792
3. mindlyticai.com — "Why your voice agent feels off (and how to fix
   turn-taking)": https://mindlyticai.com/blog/voice-ai-latency
4. dev.to/kenimo49 — "Your Voice Agent Is Slow. Here Are 5 Tricks to Hide
   It.": https://dev.to/kenimo49/your-voice-agent-is-slow-here-are-5-tricks-to-hide-it-3pcb
5. ElevenLabs latency docs: https://elevenlabs.io/docs/eleven-api/concepts/latency.mdx
   and https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization.mdx
6. Coval Flash v2.5 benchmark: https://benchmarks.coval.ai/models/eleven_flash_v2_5
7. VEXYL residential/India real-world TTFB test:
   https://vexyl.ai/elevenlabs-tts-latency-test-2026-real-world-results/
8. NVIDIA voice-agent-examples, speculative speech processing:
   https://github.com/NVIDIA/voice-agent-examples/blob/main/docs/SPECULATIVE_SPEECH_PROCESSING.md
9. Vapi prompting guide (filler/disfluency prompting):
   https://docs.vapi.ai/prompting-guide
10. UXmatters — "Designing Voice User Interfaces" (disambiguation /
    design-for-failure guidance):
    https://www.uxmatters.com/mt/archives/2018/01/designing-voice-user-interfaces.php
11. Smashing Magazine — "Designing Voice Experiences" (design for failure):
    https://www.smashingmagazine.com/2017/05/designing-voice-experiences
12. Kaizen internal docs (baseline latency numbers cited throughout):
    `CLAUDE.md`, `docs/superpowers/specs/2026-07-23-elevenlabs-tts-backend-design.md`,
    `docs/superpowers/specs/2026-07-18-first-answer-latency-warmup-design.md`,
    `docs/superpowers/specs/2026-07-19-looping-cue-design.md`

### Note on unverified/low-confidence sources
Two small GitHub repos surfaced during search ("precog", "specvoice")
illustrate the speculative-TTS/filler-caching pattern in code form, but are
single-digit-star hobby projects with no independent validation — cited only
as pattern illustrations, not as evidence of production viability. No public
information was found (in this research pass) specifically validating the
"Laya" classifier or "TypeSafe Jev" product referenced in the task context;
this report does not make claims about their specific accuracy/latency
characteristics beyond the numbers given in the task brief.
