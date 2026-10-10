# Experiments

Results of measurements and tests run against real hardware or APIs (the Pi,
Jev, Claude, STT/TTS, wake word). One file per experiment, so the numbers
survive the session that produced them and nobody has to re-run a test to
learn what it found.

File name: `YYYY-MM-DD-<short-topic>.md`. Sections: **Question**, **Method**
(where it ran, commit, exact inputs), **Results** (raw numbers, tables),
**Conclusion**, **Not tested**. Record failures and surprises too.

## Index

- [2026-10-09 Docker overhead, isolation, TTS fallback](2026-10-09-docker-overhead-and-tts-fallback.md) — Docker +~0.4s/call; hardened containers isolated; fallback first sentence ~7s
- [2026-10-10 Haiku as the fast model](2026-10-10-haiku-fast-model.md) — same tool as Sonnet 30/32 on Jev-confident skill turns; median 1,055 vs 1,511ms
- [2026-10-10 Jev tool narrowing and the cache](2026-10-10-jev-tool-narrowing.md) — routing safe (31/32), but narrowed prompts lose caching: ≈ no cost saving; 1-hour TTL is the bigger lever
- [2026-10-10 Jev criteria examples](2026-10-10-jev-criteria-examples.md) — confident+correct tool turns 115 → 120; music transport median 0.90 → 0.99; no new false stops
- [2026-10-10 Concise tool definitions](2026-10-10-concise-tool-definitions.md) — tools 3,044 → 2,089 tokens; routing 35/54 both; two wording regressions caught and fixed
- [2026-10-10 Jev archive replay](2026-10-10-jev-archive-replay.md) — 361 real requests; ≥0.8 on 77% of tool turns with no harmful misroute; median 146ms
- [2026-10-10 Slim prompt: tokens and routing](2026-10-10-slim-prompt-tokens-and-routing.md) — uncached input ~2,500 → ~100 tokens/request; tool choice 34/54 vs 32/54
- [2026-09-26 Jev category confidence probe](2026-09-26-jev-category-confidence-probe.md) — 134–270ms; paraphrases ~0.5 until criteria named examples, then 0.97–1.0
- [2026-09-26 Weather tool-first latency](2026-09-26-weather-tool-first-latency.md) — answer starts 2.7s vs 4.2–4.8s
- [2026-09-26 Wake-word scores](2026-09-26-wake-word-scores.md) — missed attempts 0.28–0.47, ambient ≤0.09
