# Feasibility Report: Laya "System 1" Decision Model on Raspberry Pi 5 for Speculative Filler-Line Classification

**Date:** 2026-09-23
**Scope:** Research-only. No code changes recommended.
**Proposed use case:** After faster-whisper STT produces a transcript, run it through Laya's `choice` primitive locally to classify intent category (weather/music/search/memory/smalltalk/etc.), then speak a canned filler line via TTS while Claude's real answer is computed in parallel — reducing perceived latency versus the current beep/warble.

---

## 1. Laya's own reported latency and RAM footprint

Per the Hugging Face model card (`convaiinnovations/laya`) and GitHub README (`NandhaKishorM/laya`):

- **Architecture:** ModernBERT-large backbone (395M params, fully fine-tuned, bidirectional) + a decision head (2 transformer layers, option-marker scorer, act/escalate head). **Total: 421M parameters.**
- **Checkpoint size:** ~808 MB for the English root checkpoint (`laya.load("convaiinnovations/laya")` downloads this); multilingual variant (mmBERT-base, 322M) is ~647 MB.
- **GPU latency:** ~33–38 ms per single-question forward pass on a T4 GPU (p50 38.4 ms, p95 42.1 ms per the README benchmark table).
- **CPU latency — the number that matters here:** The HF model card's own "Production Preload & Memory" table states explicitly:
  > `Router(preload=True)` → **32.8 ms (GPU) / 193–464 ms (CPU)**

  This is Laya's own documented figure, not a third-party estimate, and it's measured on whatever reference CPU the authors used for the doc (not ARM, not a Pi 5) — so it should be treated as a **best-case floor**, not a Pi 5 number.
- Independent comparison point: TypeSafe Jev (the closed-source model Laya benchmarks against) is reported at ~236–276 ms p50 by third-party benchmarks (AbdelStark, nibzard), which the Laya README uses to claim Laya is "6–8x faster" — but that comparison is on GPU, not CPU.

**Verdict on latency budget:** Even taking Laya's own best-case CPU figure (193 ms) at face value on unspecified (likely x86 desktop/server) hardware, a Raspberry Pi 5's Cortex-A76 cores are substantially slower per-op than typical x86 CPUs used for such benchmarks (no AVX-512, no server-class memory bandwidth, thermal-constrained clocks). Real-world ARM64 BERT-large-class CPU inference on Pi 5 for a 400M+ parameter transformer would plausibly land at 500ms–1.5s+ for a single forward pass, not the 193-464ms quoted for whatever reference machine the docs used. That already consumes most or all of a 1-2s total latency budget for what's supposed to be a *sub-second, in-parallel-with-nothing-else* operation — before accounting for model load time, tokenization, and the fact the Pi will simultaneously be running STT wrap-up and about to start TTS.

**Additional latency landmine — cold load time:** The README also states model loading takes "~22s to ~2s on CPU" after a 0.3.11 optimization (down from 22s). Even 2s of load-once latency is irrelevant if the model stays resident (as intended by `Router(preload=True)`), but it establishes that this is a genuinely heavy model to keep warm, not a lightweight classifier.

---

## 2. ARM64 / Pi 5 compatibility of the required stack

- The GitHub README states explicit floor requirements: **Python 3.10+**, and dependency versions that "set that floor": **`huggingface_hub 1.x`, `transformers 5.x`, and `torch 2.14`** (or newer) — i.e., fairly recent, cutting-edge versions of the HF stack, not older/leaner ones.
- PyTorch has official ARM64 (aarch64) wheels for Raspberry Pi 4/5 as of recent releases — confirmed by PyTorch's own tutorial ("Real Time Inference on Raspberry Pi 4 and 5", pytorch.org) which states PyTorch provides pip wheels for aarch64 and installs via plain `pip install torch torchvision`. Third-party wheel repos (Q-engineering, torch.kmtea.eu) confirm this has worked reliably since PyTorch ~1.8+.
- However: whether **torch 2.14 specifically** and **transformers 5.x** have aarch64 wheels/compatibility on Debian Bookworm ARM64 (Raspberry Pi OS) at the time of Kaizen's deployment is not something the existing sources confirm — 2.14 is a very recent/near-future PyTorch version relative to when most Pi-ARM64 wheel guides were written. This is a real unknown that would need to be checked at build time against PyPI's aarch64 wheel matrix for that specific torch/transformers pairing.
- transformers 5.x and ModernBERT support on Pi's CPU (no AVX, no CUDA, relies on generic/NEON kernels) has no independent Pi 5 benchmark found in this research — no one has published a ModernBERT-large-on-Pi-5 number.
- The Laya package by default pulls in a fairly heavy dependency chain (torch, transformers, huggingface_hub) for something that's meant to run as an ancillary classifier — this adds nontrivial install footprint and startup complexity to a project (Kaizen) that otherwise keeps its dependency surface tight (faster-whisper, Kokoro, Anthropic SDK).

**Verdict:** Torch/transformers *can* install on Pi 5 aarch64 in general (well-established), but the *specific* modern versions Laya pins (torch≥2.14, transformers 5.x) are new enough that ARM64 wheel availability and real-world stability on Raspberry Pi OS is unverified by any source found here. This is a moderate integration risk, not a hard blocker.

---

## 3. CPU contention with the existing Kaizen pipeline

- Kaizen's own project docs describe Kokoro TTS running at **1.15–1.4x slower than realtime** on the Pi 5 CPU. This is corroborated by an independent third-party benchmark (`ktomanek/edge_tts_comparison` on GitHub) which measured Kokoro on Raspberry Pi 5 CPU at **RTF 1.15–1.28x** (7.57–8.92s wall time to synthesize 6.56–6.96s of audio) — consistent with Kaizen's own figures. That same benchmark shows Piper TTS at RTF ~0.53–0.54x (much faster) on the identical Pi 5 hardware, for context on how tight Kokoro already runs relative to real time.
- No published benchmark was found of ModernBERT-large-class transformer inference *concurrently* with TTS synthesis on a Pi 5's 4 Cortex-A76 cores specifically. However, general-purpose evidence strongly supports contention risk:
  - Multiple independent Pi 5 voice-assistant build writeups (e.g. `m15-ai/TrooperAI`, DEV Community "Raspberry Pi 5 Local Voice AI") report the **CPU pegged at or near 100%** during STT+LLM+TTS stages even with lightweight models (Vosk, Piper, 0.5B-1B LLMs). One project explicitly built a lock (`self.llm_lock`) to prevent concurrent model calls after discovering that "early versions would block on a background summary and the UI would freeze for 10 seconds" — direct evidence that on a Pi 5, running two model inferences at once, even lightweight ones, materially degrades responsiveness of whichever one is on the critical path.
  - PyTorch's own Pi 5 real-time tutorial explicitly warns: "If you have anything running in the background on the Raspberry Pi it may cause contention with the model inference causing latency spikes," and recommends manually capping `torch.set_num_threads()` to trade peak throughput for reduced tail latency under contention — an implicit admission that PyTorch's default (use-all-cores) behavior causes problems when something else needs the CPU concurrently.
  - The Pi 5 has 4 Cortex-A76 cores total. faster-whisper STT, Kokoro TTS, and (in this proposal) Laya's PyTorch forward pass would all want to consume multiple threads on the *same* 4-core pool, with no isolation mechanism described in Kaizen's architecture (no cgroups/process-level CPU pinning mentioned in CLAUDE.md).

**Verdict:** Given Kokoro is documented as already running near or slightly worse than real-time on this exact hardware, and the intended trigger point for Laya inference (immediately after STT, immediately before/during Kokoro TTS start) is precisely the moment of peak CPU demand in the existing pipeline, adding a 421M-parameter PyTorch forward pass at that exact moment is very likely to either (a) slow the Laya classification itself well past the 193-464ms reference number due to contention, or (b) starve Kokoro's synthesis thread and push its RTF further past 1.0, worsening — not improving — perceived latency. This is the single biggest architectural risk in the proposal.

---

## 4. Smaller/faster Laya variants and ONNX/edge paths

Several alternate paths exist, of varying relevance to Pi 5:

- **`laya.onnx_agent.ONNXAgent`** (mentioned in the GitHub README changelog for v0.3.11): runs an exported model on ONNX Runtime. This is real and shipped, but the README frames it primarily as a GPU-adjacent "faster path" alongside `torch.compile` and a TileLang GPU fast path — no CPU-specific ONNX Runtime benchmark for Laya was found (ONNX Runtime CPU execution provider on ARM64 is generally faster than eager PyTorch for BERT-class models, so this is plausibly the more promising path if Laya were to be used at all — but no measured numbers exist for it).
- **`receptron/laya` (JS/Node ONNX port):** A third-party (not the original author's) port that runs Laya via `onnxruntime-node` with explicit `executionProviders: ["cpu"]` support, and documents an ONNX export pipeline (`export_onnx.py`) with output parity to PyTorch (~1e-5 max logit difference). This is evidence an ONNX path is technically viable and portable to ARM64/CPU, but it's a community project, not the primary maintained path, and has no published Pi 5 latency numbers either.
- **`laya-multilingual` (mmBERT-base, 322M params, ~2.2x faster than the English ModernBERT-large checkpoint per the HF card):** smaller than the default 421M model but still a 322M-parameter transformer — not a lightweight classifier by Pi-5 standards, and multilingual is irrelevant to an English-only assistant.
- **`laya-mlx` (`mizorewww/laya-mlx`):** Explicitly Apple Silicon only — requires an M-series Mac and macOS 14+. **Not applicable to Raspberry Pi 5 at all** (no ARM Linux/aarch64 MLX support; MLX is Apple's Metal-backed framework).
- **Hailo-8L NPU path:** No evidence was found that Laya, ModernBERT, or any BERT-large-class encoder has a published Hailo Dataflow Compiler (.hef) export path from the Laya project or the community. Kaizen's own roadmap notes Hailo integration for Whisper and (planned) Kokoro is nontrivial, ongoing work — adding a third custom Hailo compilation target (a 421M-param ModernBERT encoder) would be a substantial, unproven engineering effort with no existing prior art to build from.

**Verdict:** An ONNX Runtime CPU path is the most Pi-5-plausible optimization if Laya were adopted at all, but it's unbenchmarked on ARM64/Pi 5 by any source, and even at 2-4x ONNX speedup over eager PyTorch, a 421M-parameter encoder is still a heavyweight model relative to the task. laya-mlx is a dead end for Pi (Apple-only). No NPU/Hailo export path exists today.

---

## 5. Bottom-line recommendation

**Laya is not a good architectural fit for this use case on Raspberry Pi 5.**

Reasoning:

1. **Latency budget mismatch.** Laya's own best-case documented CPU latency (193-464ms, on unspecified — likely x86 server-class — hardware) already eats a large fraction of the 1-2s target for filler-line dispatch. On Pi 5's Cortex-A76 CPU specifically (no AVX, no server memory bandwidth), realistic latency for a 421M-parameter ModernBERT-large forward pass is very likely to exceed the documented range substantially, especially once contending with concurrent STT/TTS load (see point 3). A step whose entire purpose is to *shave* perceived latency should not itself risk consuming most or all of the latency budget it's trying to save.
2. **The task doesn't need Laya's power.** The actual requirement — pick 1 of ~10-20 fixed categories from a transcript to select a canned filler line — is a simple, low-cardinality intent classification problem. This is exactly the kind of task Kaizen's existing `TierRouter` (deterministic regex / Haiku micro-tier) already solves in the documented architecture at near-zero latency (regex: <5ms, no model load, no CPU contention, zero added dependency surface). Reaching for a 421M-parameter bidirectional transformer with a heavy torch/transformers dependency chain is a significant complexity and resource cost for a task an existing free, instant, already-deployed component can do.
3. **Resource contention risk is real and unaddressed.** Kokoro TTS already runs at RTF 1.15-1.4x on this same Pi 5 CPU (confirmed by both Kaizen's own docs and an independent third-party benchmark). Multiple independent Pi 5 voice-assistant projects report CPU pegged near 100% during STT+TTS+LLM stages, and at least one explicitly had to add inference locking after discovering concurrent model calls froze the UI for seconds. Inserting a heavyweight PyTorch inference call at exactly the moment the pipeline is about to start (CPU-bound) TTS synthesis is the worst possible placement for a new CPU workload in this architecture.
4. **No Pi-5-appropriate fast path exists today.** The ONNX Runtime path (`ONNXAgent`) is unbenchmarked on ARM64/CPU; laya-mlx is Apple-only; no Hailo/NPU export path exists. There is no evidence-backed way to get Laya's latency down to something clearly compatible with a sub-1s, every-turn budget on this hardware.
5. **Version/compatibility risk adds engineering overhead without payoff.** Laya's own floor requirements (torch≥2.14, transformers 5.x, Python 3.10+) are recent enough that ARM64/Raspberry Pi OS wheel availability and stability are unverified, adding integration risk on top of the performance risk, for a component whose value proposition (better filler-line selection than the existing regex/Haiku router) is unproven to justify that risk.

**Recommendation:** Do not adopt Laya for this purpose. The existing deterministic regex/TierRouter classification path already meets the "fast, zero-added-latency, fixed-category" requirement this feature needs, and doing so without adding a new heavyweight ML dependency or CPU contention risk to an already latency-tight, CPU-constrained pipeline. If richer intent classification is desired in the future, the more promising directions (not explored further here, per research-only scope) would be evaluating whether the Hailo-8L NPU roadmap items already planned for Kaizen (Whisper, Kokoro) could eventually host a much smaller, purpose-built classifier — rather than adopting a general-purpose 421M-parameter model whose own documentation targets GPU deployment as the primary path.

---

## Sources

- Laya GitHub README: https://github.com/NandhaKishorM/laya (and https://github.com/NandhaKishorM/laya/blob/main/README.md)
- Laya HF model card (main): https://huggingface.co/convaiinnovations/laya
- Laya HF model card (CPU latency table / "Production Preload & Memory"): https://huggingface.co/convaiinnovations/laya?local-app=llama.cpp
- laya-mlx (Apple Silicon only): https://github.com/mizorewww/laya-mlx
- receptron/laya (community ONNX/Node port): https://github.com/receptron/laya
- TypeSafe Jev independent CPU benchmarks cited by Laya: https://github.com/AbdelStark/jev-benchmarks, https://github.com/nibzard/decision-model-benchmark
- PyTorch official Pi 4/5 real-time inference tutorial (ARM64 wheels, thread contention warning): https://pytorch.org/tutorials/intermediate/realtime_rpi.html
- Q-engineering PyTorch-on-Pi-5 install guide: https://qengineering.eu/install%20pytorch%20on%20raspberry%20pi%205.html
- Kokoro TTS on Raspberry Pi 5 CPU independent benchmark (RTF 1.15-1.28x): https://github.com/ktomanek/edge_tts_comparison
- Pi 5 voice-assistant CPU-saturation reports: https://github.com/m15-ai/TrooperAI (README), https://dev.to/pat9000/raspberry-pi-5-local-voice-ai-what-works-in-2026-21ic
- Kaizen project docs (Kokoro RTF figures, architecture): CLAUDE.md / WORKING_MEMORY.md in this repository
