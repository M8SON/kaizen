---
name: music-control
description: Transport controls (stop, pause, resume, skip, volume) for whatever music is currently playing — Spotify or SoundCloud. Routes to the active source automatically.
---

# Music Control Skill

## When to use

Use for transport commands while music is already playing:

- "stop", "stop music", "halt"
- "pause", "pause the music"
- "resume", "continue", "unpause"
- "skip", "next track"
- "volume up", "louder", "turn it up"
- "volume down", "quieter", "turn it down"

This skill does NOT start music. Use `spotify` or `soundcloud` for that.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [stop, pause, resume, skip, volume_up, volume_down]
    description: Transport command to issue against whichever source is playing.
required:
  - action
```

## How to respond

Brief acknowledgement: "Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up.", "Volume down.". If nothing is playing, say so plainly ("Nothing is playing.").
