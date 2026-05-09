---
name: soundcloud
description: Play, stop, pause, resume, skip, or adjust volume on SoundCloud music. A play
  request queues 20 tracks matching the query; subsequent skip commands advance through
  the queue.
---
# SoundCloud Skill

## When to use

This skill is the secondary music backend, used only when SoundCloud is specifically what the user wants. Prefer the `spotify` skill for everyday "play me X" requests; SoundCloud doesn't have most major-label catalog and is best for niche / DJ content.

Pick this skill when the user's phrasing includes any of:
- "remix", "bootleg", "mashup"
- "DJ set", "live set"
- "on SoundCloud" / "from SoundCloud"
- An obscure artist Spotify doesn't have

Transport (stop/pause/skip/volume) is handled by `music-control`, not this skill — don't call this skill's transport actions directly. The transport actions are kept in this file's input schema for backward compat only; new code routes via `music-control`.

Triggers covered by this skill:
- **Play music** — "play remix of [song]", "play [DJ name] live set", "play that [thing] on SoundCloud"

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, stop, pause, resume, skip, volume_up, volume_down]
    description: The transport command to issue. Defaults to play.
  query:
    type: string
    description: Song name, artist, or genre. Required when action is play.
required:
  - action
```

## How to respond

For play, confirm the genre/song. For stop / pause / resume / skip / volume, brief
acknowledgement ("Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up."). If
nothing is playing for a transport command, say so plainly.
