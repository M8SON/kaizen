# Weather tool-first latency (on the Pi)

**Date:** 2026-09-26 (~14:40 UTC)
**Where:** Raspberry Pi, real Claude API and real weather API
**Code:** `df73c91` (`TOOL_FIRST_ENABLED`)
**Recovered from:** Claude Code transcript `61d1d7cc-f99e-48ac-b406-5e5047ae3c43` (written up 2026-10-10)

## Question

When Jev says `weather`, is it faster for Kaizen to run the weather tool before
calling Claude (Claude only phrases the answer) than to let Claude pick the tool?

## Method

Same weather request, 3 runs per mode, timed on the Pi.

## Results

| Mode | Answer starts | Total |
|---|---|---|
| Before (Claude picks the tool) | 4.2–4.8s | 5.1–5.5s |
| Tool-first | 2.7s | 3.5–3.7s |

About 1.7s faster to first audio. It also removed the duplicate "I'll get the
weather..." line after the filler.

Earlier baseline from live logs (02:28 UTC, before tool-first): weather turn was
6.5s after the filler (Claude 2.0s + weather tool 2.3s + Claude 2.2s); Jev took 220ms.

## Not tested

Other cities or days ("weather in Boston tomorrow") still prefetch the default
location first, then Claude calls the tool again; that path was not timed.
