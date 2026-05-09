---
name: spotify
description: Play music or saved playlists from Spotify. Default music backend — use this for "play X" / "play [artist]" / "play my [playlist]" requests unless explicitly asked for SoundCloud or for remixes/bootlegs/mashups.
metadata:
  miniclaw:
    requires:
      env:
        - SPOTIFY_CLIENT_ID
        - SPOTIFY_CLIENT_SECRET
---

# Spotify Skill

## When to use

This is the DEFAULT music source. Prefer it for:

- **Play a track or artist** — "play [song]", "play [artist]", "put on some [genre]"
- **Play a saved playlist** — "play my [name] playlist", "play my COUNTRY", "start my morning playlist"

For DJ remixes, bootlegs, mashups, or specific SoundCloud tracks, use the `soundcloud` skill instead. Trigger words that indicate SoundCloud: "remix", "bootleg", "mashup", "DJ set", "live set", or "on SoundCloud".

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, play_playlist]
    description: play searches the catalog; play_playlist plays a saved user playlist by name.
  query:
    type: string
    description: For play action — song / artist / genre query.
  name:
    type: string
    description: For play_playlist action — playlist name (fuzzy matched against the user's saved playlists).
required:
  - action
```

## How to respond

For `play`, confirm what's playing ("Now playing X by Y"). For `play_playlist`, confirm the playlist name. If setup is incomplete or the Connect device is unavailable, relay the error verbatim — it tells the user what to fix.

## Setup (one-time)

Before this skill works:

1. Register a Spotify Developer app at developer.spotify.com.
2. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`.
3. Add `http://localhost:8888/callback` to the dev app's Redirect URIs.
4. Run `python scripts/spotify_login.py` once and follow the browser flow.
5. On the Pi: `apt install librespot`, then open phone Spotify → tap Connect icon → tap Pi as a device once to pair librespot.
