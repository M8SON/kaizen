---
name: spotify
description: Play songs, artists, genres, moods or the user's playlists on Spotify. The default for music.
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
- **Restart Spotify** — "restart Spotify", "restart librespot", "Spotify isn't working", or a bare "restart" → `restart` (restarts the Pi's Spotify Connect service; use when the Pi doesn't show up as a device or playback silently fails)

For DJ remixes, bootlegs, mashups, or specific SoundCloud tracks, use the `soundcloud` skill instead. Trigger words that indicate SoundCloud: "remix", "bootleg", "mashup", "DJ set", "live set", or "on SoundCloud".

## Tool notes

play_genre for a genre or mood (play stops after one song); for a vague request like "play some music", pick one rather than asking. restart resets the Pi's Spotify Connect when playback fails. Relay errors as-is.

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, play_genre, play_playlist, restart]
  query:
    type: string
    description: Song, artist, genre or mood
  name:
    type: string
    description: Playlist name
required:
  - action
```

## How to respond

For `play`, confirm what's playing ("Now playing X by Y"). For `play_genre`, confirm the genre briefly ("Playing some EDM"). For `play_playlist`, confirm the playlist name. For `restart`, say it's restarted and that the Pi should show up in Spotify again in a few seconds. If setup is incomplete or the Connect device is unavailable, relay the error verbatim — it tells the user what to fix.

## Setup (one-time)

Before this skill works:

1. Register a Spotify Developer app at developer.spotify.com.
2. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`.
3. Add `http://localhost:8888/callback` to the dev app's Redirect URIs.
4. Run `python scripts/spotify_login.py` once and follow the browser flow.
5. On the Pi: `apt install librespot`, then open phone Spotify → tap Connect icon → tap Pi as a device once to pair librespot.
