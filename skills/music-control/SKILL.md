---
name: music-control
description: Stop, pause, resume, skip or change the volume of whatever music is playing (Spotify or SoundCloud).
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

## Tool notes

Call it directly; it finds the player and reports if nothing is playing.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [stop, pause, resume, skip, volume_up, volume_down]
required:
  - action
```

## How to respond

Brief acknowledgement: "Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up.", "Volume down.". If nothing is playing, say so plainly ("Nothing is playing.").
