---
name: spotify
description: Play music, genres, or saved playlists from Spotify. Default music backend — use this for "play X" / "play [artist]" / "put on some [genre]" / "play my [playlist]" requests unless explicitly asked for SoundCloud or for remixes/bootlegs/mashups.
metadata:
  kaizen:
    requires:
      env:
        - SPOTIFY_CLIENT_ID
        - SPOTIFY_CLIENT_SECRET
---

# Spotify Skill

## When to use

This is the DEFAULT music source. Prefer it for:

- **Play a specific track or artist** — "play [song]", "play [artist]" → `play`
- **Play a genre, mood, or vibe** — "play some EDM", "put on country", "play chill music", "something to study to" → `play_genre` (continuous, shuffled playlist — `play` would stop after one song)
- **Play a saved playlist** — "play my [name] playlist", "play my COUNTRY", "start my morning playlist"

For DJ remixes, bootlegs, mashups, or specific SoundCloud tracks, use the `soundcloud` skill instead. Trigger words that indicate SoundCloud: "remix", "bootleg", "mashup", "DJ set", "live set", or "on SoundCloud".

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, play_genre, play_playlist]
    description: play plays one specific song/artist match; play_genre plays a genre or mood continuously from a shuffled playlist; play_playlist plays a saved user playlist by name.
  query:
    type: string
    description: For play — song / artist query. For play_genre — the genre or mood (e.g. "edm", "country", "chill").
  name:
    type: string
    description: For play_playlist action — playlist name (fuzzy matched against the user's saved playlists).
required:
  - action
```

## How to respond

For `play`, confirm what's playing ("Now playing X by Y"). For `play_genre`, confirm the genre briefly ("Playing some EDM"). For `play_playlist`, confirm the playlist name. If setup is incomplete or the Connect device is unavailable, relay the error verbatim — it tells the user what to fix.

## Setup (one-time)

Before this skill works:

1. Register a Spotify Developer app at developer.spotify.com.
2. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`.
3. Add `http://localhost:8888/callback` to the dev app's Redirect URIs.
4. Run `python scripts/spotify_login.py` once and follow the browser flow.
5. On the Pi: `apt install librespot`, then open phone Spotify → tap Connect icon → tap Pi as a device once to pair librespot.
