# Wake-word scores for missed "hey jarvis" attempts (on the Pi)

**Date:** 2026-09-26 (~21:40 UTC)
**Where:** Raspberry Pi, reSpeaker XVF3800, openWakeWord `hey_jarvis`
**Code:** `28c9f21` (two-stage wake)
**Recovered from:** Claude Code transcript `61d1d7cc-f99e-48ac-b406-5e5047ae3c43` (written up 2026-10-10)

## Question

Were missed wake words a deaf mic, a frozen loop, or low detector confidence?

## Method

Per-frame wake scores logged on the Pi while Mason said "hey jarvis" at about
1 ft; ambient scores taken from ~2.5 hours of logs, quiet and with music.

## Results

| Sample | Wake score |
|---|---|
| 4 missed attempts at 1 ft (17:36–17:37 local) | 0.45, 0.47, 0.28, 0.36 |
| Background, quiet or music, ~2.5 hours | never above 0.09 |
| Threshold at the time | 0.7 |

## Conclusion

Real attempts often score between ambient and the 0.7 threshold. Shipped a
soft threshold of 0.25 (`WAKE_WORD_SOFT_THRESHOLD`): scores 0.25–0.7 start
listening but only count if the transcript contains "Jarvis". Barge-in still
needs a full-threshold score.
