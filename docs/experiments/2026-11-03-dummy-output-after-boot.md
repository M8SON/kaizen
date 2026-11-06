# No speech because PipeWire fell back to Dummy Output after boot (on the Pi)

**Date:** 2026-11-03
**Where:** Raspberry Pi (`daedalus`), reSpeaker XVF3800, user `kaizen.service`, on ethernet
**Code:** `e422b7f`, no code changes

## Question

Kaizen hears and answers requests in the logs, but nothing is audible. Why?

## Method

Read the journal and `wpctl status`, then restarted services in two different orders and
checked the default sink and `/proc/asound/card2/pcm0p/sub0/status`.

## Raw results

| When | State | Result |
|---|---|---|
| 07:48:09 (boot) | PipeWire probing the card | `'front:2': playback open failed: Device or resource busy`; sink creation failed |
| 07:50-08:00 | Sink = `Dummy Output` (`auto_null`) | Transcripts, Claude and TTS all logged as normal; TTS and librespot both routed to the dummy |
| 08:01:40 | `restart wireplumber`, Kaizen running (holds `hw:2,0` capture) | Worse: reSpeaker device, sink and source all gone from PipeWire |
| 08:02 | `stop kaizen`, `restart wireplumber` | reSpeaker sink and source back; sink is the default |
| 08:02:26 | `start kaizen` | Startup speech: playback PCM `RUNNING` in 9 samples; `Waiting for wake word` at 08:02:50 |

Each ordering was run once.

## Conclusion

TTS "succeeds" against the dummy sink, so Kaizen's logs look healthy while nothing plays.
Recovery order matters: stop Kaizen, restart WirePlumber, start Kaizen.

## Self-heal test (`scripts/ensure_audio.sh`, uncommitted on top of `e422b7f`)

| When | Fault | Result |
|---|---|---|
| 09:26:15 | `restart wireplumber` with Kaizen running: sink = `auto_null` | Timer fired 09:27:17, confirmed for 15s, restarted Kaizen 09:27:33; real sink restored, startup speech 09:27:54, ~100s total. Sink returned when Kaizen released the mic, so the WirePlumber restart step did not run |
| 09:30:55 | Kaizen stopped, `aplay` holding `hw:2,0` playback while WirePlumber starts: sink = `auto_null` | Script waited 10s, restarted WirePlumber at 09:31:10, sink returned 09:31:16 (1s after `aplay` was killed at 09:31:15). Kaizen started and spoke 09:31:37 |

The 09:26 row tested a once-a-minute watch timer that was then removed as overkill; only the
startup check was kept, so a sink lost while Kaizen is running is not detected.

The `aplay` fault did not log the boot's `Failed to create` line and healed when the holder went
away, whereas the boot fault stayed on the dummy for 14 minutes with the PCM free. It is close to
the boot fault, not identical.

## Real reboot with the fix (2026-11-06, `cf157f5`)

`sudo reboot` at ~13:19, on ethernet. PipeWire and WirePlumber started 13:20:21; no
`playback open failed` or `Failed to create` this boot. `ensure_audio.sh` found the reSpeaker
sink on its first check (`kaizen.service` Starting 13:20:21 → Started 13:20:24, no WirePlumber
restart logged). Startup speech 13:21:45, playback PCM `RUNNING`. The boot fault did not recur,
so the recovery path is still only tested against the injected fault.

## Not tested

- What held the playback PCM at 07:48:09. Unknown; the journal is not persistent and the holder
  was not captured, so this can recur on any boot.
- Audible output: PCM state was observed, not sound from the speaker.
- The recovery path on a real boot where the fault occurs (one clean reboot so far, 1 of 2 boots
  observed had the fault).
