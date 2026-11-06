# 2026-11-06 What each API reports about its own usage

## Question

Can Kaizen meter its spend from what each service returns per call, without
knowing the account's plans?

## Method

On the Pi (`daedalus`), Kaizen's venv, keys from `~/kaizen/.env`. First a
probe of the raw responses, then the new `core/usage_log.py` hooks run from a
scratch copy of the working tree (uncommitted, on top of `e09affa`) with
`USAGE_LOG_PATH=/tmp/kz-usage.db`, so the live service and its DB were not touched.

- ElevenLabs: `text_to_speech.with_raw_response.stream`, Flash v2.5, `pcm_24000`.
- Jev: `system_one` with one Choice question; then the real `FillerClassifier.classify("what's the weather tomorrow")`.
- Meta: a 1.44 s cached filler clip ("Checking the weather now.", resampled to 16 kHz) streamed through the real `MetaSttSession`.
- Claude: one `messages.create` on `claude-haiku-5-5`.

## Results

| Service | Field | Value |
|---|---|---|
| ElevenLabs | `character-cost` response header | 2 for "Hello there." (12 chars); 7 for "Testing the usage log, one two three." (37 chars) |
| ElevenLabs | `GET /v1/user/subscription` | 401: the key lacks `user_read`, so the plan can't be read |
| Jev | `response.usage`, `response.model` | 316 in / 31 out (probe); 1,047 in / 100 out (real classifier, all categories); `jev-1.14.0` |
| Meta | `audioProcessedMs` on the final transcript | 1440 for 1,439 ms sent |
| Claude | `response.usage` | 13 in / 14 out |

All four rows landed in the usage DB and `scripts/cost_report.py` priced them
(total $0.0008).

## Conclusion

Every billed service reports exact per-call usage, so metering needs no plan
details. The one thing not knowable from the API is ElevenLabs dollars per
credit (plan-dependent; the key can't read the subscription), so the report
counts the Starter plan fee ($6/month, 30,000 credits) and shows the
share of the allowance used. ElevenLabs bills far fewer
credits than characters on this account (~0.2 per char), so estimating from
text length would have overstated it ~2.5-5x.

## Not tested

- The tool-loop and greeting hooks against live traffic (unit-tested; same `response.usage` object).
- Whether Meta bills aborted (no-speech) sessions; they are logged by bytes sent.
- Prices themselves come from third-party pages, not the vendors' consoles.
