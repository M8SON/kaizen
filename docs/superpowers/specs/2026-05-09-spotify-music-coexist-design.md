# Spotify + SoundCloud Music Coexistence — Design Spec

**Date:** 2026-05-09
**Status:** Approved (awaiting implementation plan)
**Owner:** Mason (`M8SON`)

## Context

The current `soundcloud` skill handles all music playback through `yt-dlp scsearch` + `mpv`. Pi 5 testing on 2026-05-08 / 2026-05-09 surfaced a structural problem: SoundCloud's catalog is fundamentally weak for mainstream music. Most major-label artists (Chris Stapleton, Luke Combs, Morgan Wallen, etc.) aren't on SoundCloud officially, and `yt-dlp`'s SoundCloud extractor regularly hits HTTP 404 on individual track metadata fetches due to anti-bot / region / registered-user restrictions. End result: `play me country music` returns "No results found" most of the time.

Mason has a Spotify Premium subscription. Spotify has the actual catalog of mainstream music. SoundCloud, however, remains the better source for DJ remixes, bootlegs, mashups, and unsigned-artist tracks.

This spec adds Spotify as a music backend alongside SoundCloud. Both coexist; Spotify is the default, SoundCloud is reached via explicit phrasing ("on soundcloud", "remix of", "bootleg", "mashup").

## Goals

- Voice command "play me [artist/song/genre]" reliably plays mainstream music via Spotify.
- Voice command "play my [playlist name]" plays a saved Spotify playlist (fuzzy match).
- SoundCloud remains reachable via trigger words for the cases it serves better.
- Transport commands (`stop`, `pause`, `resume`, `skip`, `volume_up`, `volume_down`) work uniformly regardless of which backend is currently playing.
- Only one music source plays at a time. Starting one stops the other automatically.
- The skill self-gates with a clear setup message when env vars / tokens / librespot aren't configured (same pattern as Homebridge).

## Non-Goals

- "Like this song", "save to liked songs", "go to radio", or other Spotify-specific extras. Out of scope for the initial ship.
- Integration with other music sources (Apple Music, YouTube, local files). Approach A leaves room to add these later without touching existing skills.
- Automated CI for the Spotify path. Live credentials make CI flaky; manual Pi smoke test serves as integration.
- Replacing the existing `soundcloud` skill. Kept as-is.

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                     User voice                         │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│               TierRouter (intent dispatch)             │
├────────────────────────────────────────────────────────┤
│  "stop" / "pause" / "skip" / "volume up"  ──► music-control
│  "play remix of …" / "on soundcloud …"    ──► soundcloud (LLM hint)
│  "play …" (everything else)               ──► spotify (LLM hint)
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────┐    ┌──────────────┐   ┌──────────────────┐
│ spotify skill│    │soundcloud    │   │ music-control    │
│ play, play_  │    │  skill       │   │   skill          │
│ playlist     │    │ play         │   │ stop/pause/skip/ │
│              │    │              │   │ resume/volume_*  │
└──────┬───────┘    └──────┬───────┘   └────────┬─────────┘
       │                   │                    │
       │     _stop_all_music() then              │
       │     set _active_music_source            │
       └────────────────────┬───────────────────┘
                            ▼
       ┌────────────────────────────────────────────┐
       │ ContainerManager._active_music_source      │
       │   "spotify" | "soundcloud" | None          │
       └────────────────────────────────────────────┘
                            ▼
            ┌───────────────┴────────────────┐
            ▼                                ▼
   ┌────────────────┐              ┌────────────────────┐
   │ Spotify Web API│              │ mpv subprocess     │
   │  (spotipy)     │              │  (yt-dlp + sock)   │
   └────────┬───────┘              └─────────┬──────────┘
            ▼                                ▼
   ┌────────────────┐              ┌────────────────────┐
   │  librespot     │              │ ALSA → KT USB DAC  │
   │  (Pi daemon,   │              └────────────────────┘
   │   Spotify      │
   │   Connect)     │
   └────────┬───────┘
            ▼
   ┌────────────────┐
   │ ALSA → KT USB  │
   │      DAC       │
   └────────────────┘
```

**Three skills, one shared piece of state:**

- `spotify` and `soundcloud` are play-source skills — each starts music from its backend.
- `music-control` is a tiny pure-routing skill — reads `_active_music_source` and dispatches transport commands to the right backend.
- ContainerManager owns the active-source flag (mutated only by play actions and end-of-playback callbacks).

**Two audio backends, one mutual-exclusion rule:** only one backend plays at a time. Each play action calls `_stop_all_music()` first.

## Components

### 1. `spotify` skill (new)

`skills/spotify/SKILL.md` + `_execute_spotify` handler in `core/container_manager.py`.

- Frontmatter: `name: spotify`, `type: native`, declares `requires.env: [SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET]`.
- Two actions:
  - `play` (input: `query: string`) — searches the catalog, plays the top-matching track.
  - `play_playlist` (input: `name: string`) — fuzzy-matches against the user's saved playlists, plays as a context.
- Handler uses `spotipy` to:
  - `sp.search(q=query, type='track', limit=10)` → pick top track URI.
  - `sp.current_user_playlists()` (paginated to 50/page) → fuzzy match (see below).
  - Find an active Spotify Connect device (librespot on the Pi) via `sp.devices()`.
  - Start playback via `sp.start_playback(device_id=…, uris=[track_uri])` or `context_uri=playlist_uri`.
- **Playlist fuzzy match algorithm:**
  1. Normalise both query and each playlist name to lowercase.
  2. Exact match wins (case-insensitive).
  3. If no exact match, prefer substring match (query is a substring of name OR name is a substring of query).
  4. If no substring match, score by Levenshtein distance, return best if distance ≤ 3 OR ≤ 30% of the longer length.
  5. Otherwise return "couldn't find a playlist named X".

  Mason's playlist naming convention (COUNTRY, HIPHOP, etc. — uppercase genre words) means step 2 or 3 will resolve almost every reasonable query.
- Token management lives in `core/spotify_auth.py` — load cached tokens, auto-refresh, raise a clear error if not yet authorized.

`SKILL.md` `When to use` documents that Spotify is the default music source. SoundCloud trigger words ("remix", "bootleg", "mashup", "on soundcloud") are documented in the **soundcloud** skill's `When to use` so the LLM correctly routes those phrasings to SoundCloud.

### 2. `music-control` skill (new)

`skills/music-control/SKILL.md` + `_execute_music_control` handler in `core/container_manager.py`.

- Frontmatter: `name: music-control`, `type: native`, no env requirements.
- Single skill, six actions: `stop`, `pause`, `resume`, `skip`, `volume_up`, `volume_down`.
- Handler reads `ContainerManager._active_music_source`, dispatches via a small lookup table to the matching backend's transport function.
- Returns `"Nothing is playing."` when `_active_music_source is None`.
- ~50 lines including the dispatch table.

### 3. `soundcloud` skill (existing, light edits)

`skills/soundcloud/SKILL.md` + `_execute_soundcloud` handler.

- The `play` action stays. Transport actions (`stop`, `pause`, etc.) stay in code for backward compat — Claude can still call them if it explicitly chooses, but `TierRouter` dispatch patterns no longer route to them.
- Only change: `play` now calls `ContainerManager._stop_all_music()` first instead of just `_terminate_mpv_process()`. Same effect for "stop existing soundcloud" but also stops Spotify if it's currently active.
- `SKILL.md` `When to use` updated: documents that this skill is for remixes/bootlegs/mashups/etc., not the default music source.

### 4. ContainerManager additions

`core/container_manager.py`:

- New attribute: `self._active_music_source: str | None = None`
- New method: `_stop_all_music()` — calls `_stop_mpv()` AND `_stop_spotify_playback()`, clears `_active_music_source`. Idempotent and exception-safe (each leg is wrapped to swallow its own errors).
- New method: `_stop_spotify_playback()` — uses `core/spotify_auth.get_spotify_client()` to call `sp.pause_playback(device_id=…)` if Spotify is the active source. Best-effort: swallows network errors (we're tearing down anyway).
- `_execute_spotify` and `_execute_soundcloud` set `_active_music_source` after play succeeds.
- `_execute_music_control` reads `_active_music_source` to dispatch.

### 5. Spotify auth + token cache

`core/spotify_auth.py` + `scripts/spotify_login.py`.

- `scripts/spotify_login.py` is a one-time setup script. It:
  - Reads `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` from env (`.env` loaded via `python-dotenv`).
  - Spins up a tiny local HTTP server on the redirect URI's port.
  - Prints the Spotify authorize URL with required scopes (`user-modify-playback-state user-read-playback-state user-read-private playlist-read-private user-library-read`).
  - User opens the URL in any browser, approves, gets redirected to `http://localhost:8888/callback?code=…`.
  - Server catches the redirect, exchanges the code for tokens, writes `~/.miniclaw/spotify-tokens.json` (atomic: write to `.tmp` + rename).
  - Prints success message and exits.
- `core/spotify_auth.get_spotify_client()`:
  - Reads `~/.miniclaw/spotify-tokens.json`.
  - Returns a `spotipy.Spotify` instance with auth managed by `spotipy.SpotifyOAuth(open_browser=False, cache_handler=…)` so refresh is automatic on every call.
  - Raises `SpotifyAuthMissing` if env vars or token cache are missing — caller surfaces a friendly message.

### 6. TierRouter dispatch patterns

`config/intent_patterns.yaml`:

- Existing transport patterns (`^(stop|halt)…`, `^pause…`, `^(resume|continue|unpause)…`, `^(skip|next)…`, `^(volume up|turn it up|louder)…`, `^(volume down|turn it down|quieter)…`) repointed from `soundcloud` → `music-control`. Same regexes, new target.
- No new escalate patterns needed — Claude/Haiku judgment based on each skill's `SKILL.md` content handles spotify-vs-soundcloud routing for play queries.

### 7. librespot daemon (Pi-side, not in repo)

- Mason runs `apt install librespot` on the Pi.
- Optional but recommended: enable as a systemd user service so it starts on boot. We document this in `skills/spotify/SKILL.md` setup section and in this spec, but no Python code touches librespot directly — we only address it via the Spotify Web API by referencing its device id.
- librespot pairs with Mason's account via Spotify Connect Zeroconf: open Spotify on phone → tap Connect icon → tap Pi (listed as a Connect speaker) once. After that, `sp.devices()` returns librespot's device id and start_playback(device_id=…) routes audio to it.

## Data Flow

### A. First-time setup (one-time, manual)

1. Mason adds `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env`.
2. Mason adds `http://localhost:8888/callback` to "Redirect URIs" in his Spotify dev app.
3. Run `python scripts/spotify_login.py`:
   - Prints authorize URL.
   - Mason opens URL in browser, approves consent.
   - Spotify redirects to `localhost:8888/callback?code=…`.
   - Script exchanges code → access + refresh tokens.
   - Writes `~/.miniclaw/spotify-tokens.json` atomically.
4. On Pi: `apt install librespot`. Open phone Spotify, tap Connect icon, tap Pi as device once.
5. Restart MiniClaw — `spotify` skill is now in eligible skills, no longer in `skipped_skills`.

### B. "Play me country music"

```
voice → STT → Claude/Haiku
   ↓ tool_call: spotify({action: "play", query: "country music"})
_execute_spotify:
   1. get_spotify_client()           ← reads cached refresh token, auto-refreshes
   2. sp.search(q="country music", type="track", limit=10) → list of tracks
   3. ContainerManager._stop_all_music()  ← no-op if nothing playing
   4. find_spotify_device()          ← matches librespot's device name
   5. sp.start_playback(device_id=…, uris=[top_track_uri])
   6. self._active_music_source = "spotify"
   7. return f"Now playing: {track['name']} by {track['artists'][0]['name']}"
```

### C. "Play my COUNTRY playlist"

```
voice → STT → Claude/Haiku
   ↓ tool_call: spotify({action: "play_playlist", name: "COUNTRY"})
_execute_spotify (play_playlist branch):
   1. get_spotify_client()
   2. sp.current_user_playlists()    ← returns 50/page; paginate if needed
   3. fuzzy_match("COUNTRY", playlist_names) → best match
   4. _stop_all_music()
   5. sp.start_playback(device_id=…, context_uri=playlist_uri)
   6. self._active_music_source = "spotify"
   7. return f"Playing your {matched_playlist_name} playlist"
```

### D. "Skip" (transport while Spotify is playing)

```
voice → STT → TierRouter
   ↓ dispatch_pattern "^(skip|next)…" matches → tier=direct, skill=music-control
_execute_music_control({action: "skip"}):
   1. source = self._active_music_source   ← "spotify"
   2. dispatch_table[source].next() → sp.next_track(device_id=…)
   3. return "Skipped."
```

→ Bypasses the LLM entirely — sub-second response.

### E. "Play me a remix of Despacito" (SoundCloud trigger)

```
voice → STT → Claude/Haiku (sees "remix" trigger word in soundcloud's SKILL.md)
   ↓ tool_call: soundcloud({action: "play", query: "Despacito remix"})
_execute_soundcloud (existing flow, one tweak):
   1. ContainerManager._stop_all_music()   ← stops Spotify if active
   2. yt-dlp scsearch20 + mpv as today
   3. self._active_music_source = "soundcloud"
   4. return "Now playing: <track>"
```

### F. First voice request before setup is done

```
voice → STT → Claude/Haiku
   System prompt has:
     --- Unavailable Skills (installed but missing requirements) ---
     - spotify: needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars
       set in .env, plus scripts/spotify_login.py to be run once.
   →  Claude responds: "I'd love to play that, but Spotify isn't set up yet.
       You'll need to add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to your
       .env file and run the login script. Want me to walk you through it?"
```

### Practical wrinkles

- **Spotify Connect device discovery** — every play needs to find librespot's device id from `sp.devices()`. Cached in `core/container_manager.py` after first lookup; re-queried only if the cached id stops working (404 from start_playback).
- **Empty search results** — if `sp.search(...)` returns zero tracks (rare but possible for very obscure queries), return `"Couldn't find anything matching '<query>' on Spotify."` Claude can then suggest fallback to SoundCloud.

## Error Handling

Catalog of failure modes and responses. Spec one-liner: **any failure must produce a tool result string Claude/Haiku can read aloud. Never raise into the tool loop, never return `None`.**

### Auth-layer failures (user-fixable, surface clear actions)

- Missing env vars → skill in `skipped_skills` with reason. Section F flow above.
- Token cache file missing → same skipped state, reason includes "run scripts/spotify_login.py".
- Refresh token revoked / expired → caught by `get_spotify_client()`, logged at WARNING, returns `"Spotify access has been revoked. Run scripts/spotify_login.py to re-authorize."` to Claude as the tool result.

### Runtime API failures (Spotify side)

- Empty search results → `"Couldn't find anything matching '<query>' on Spotify."`
- HTTP 401 mid-call (token race condition) → one retry after refresh; on second 401 return `"Spotify auth expired — please re-run scripts/spotify_login.py."`
- HTTP 429 rate limit → return `"Spotify is rate-limiting requests; try again in a moment."` (don't retry — could compound).
- Network error / timeout → return `"Couldn't reach Spotify right now."`

### librespot / Connect device failures

- No active device when `sp.start_playback` is called → return `"Spotify is set up but no Connect device is available. Check that librespot is running on the Pi."` Claude can then suggest restarting it.
- Cached device id stale (librespot restarted with new id) → catch the `404 Device not found` from start_playback, refresh `sp.devices()` once, retry once. If still no device, fall through to the message above.
- librespot crashes mid-playback → no API hook to detect this; the next transport command will hit "no active device". Acceptable — same UX as walking away from a Connect speaker.

### State consistency

- `_active_music_source` invariant: only set after a play action confirms success (Spotify returned 2xx, mpv subprocess started). If a play fails partway, source stays at its previous value or `None`. Prevents transport commands targeting a backend that didn't actually start.
- `_stop_all_music()` on any play action: ALWAYS called, even if the new play is the same source (idempotent — stops mpv if no mpv, no-op).

### music-control edge cases

- Transport command (e.g. "skip") fires while `_active_music_source is None` → return `"Nothing is playing."`
- Transport command fires for a backend that's been killed externally (kill mpv from terminal) → backend's transport call surfaces an error, music-control returns the error verbatim. We don't try to detect external death; rare, debugging cost > UX cost.

## Testing

Bias toward unit tests with mocked Spotify. Smoke test on Pi for the integration.

### Unit tests (fast, deterministic, no network)

- `tests/test_spotify_skill.py`:
  - Search returns top-track URI; start_playback called with right args; result string formatted correctly.
  - Empty search returns the friendly message, doesn't call start_playback.
  - Playlist match: fuzzy match picks COUNTRY for "country" query.
  - Playlist not found: returns "couldn't find a playlist named X".
  - 401 retry path: first call 401, refresh, second call 200, success.
  - Stale device id retry path: first call 404, devices() refresh, second call 200.
- `tests/test_music_control.py`:
  - Active source = spotify → "skip" calls spotify next_track, not mpv.
  - Active source = soundcloud → "skip" calls mpv playlist-next.
  - Active source = None → returns "Nothing is playing."
- `tests/test_spotify_auth.py`:
  - Loads tokens from cache file; calls refresh when access token expired.
  - Returns clean error when cache missing.
  - Token cache write is atomic (tmp file + rename).
- Existing `tests/test_soundcloud_handler.py` extended with one test: play action calls `_stop_all_music()` first.

### Integration / manual Pi smoke

Documented checklist in this spec, not automated:

1. Setup steps (env vars, login script, librespot install) — verify each completes without error.
2. "Play me country music" → music starts within 2-3s.
3. "Play my COUNTRY playlist" → playlist starts.
4. "Skip" → next track, no error.
5. "Stop" → silence.
6. "Play remix of [song]" → routes to SoundCloud (correctness of trigger words).
7. Then "Skip" → skips the SoundCloud track (active source switched correctly).
8. Disconnect Pi from network → "Play X" → friendly error message, no crash.

### No CI for the Spotify integration

Live credentials make CI flaky and risk token leaks. Pi smoke test serves as integration. Same posture we already use for the dashboard skill and Hailo runtime tests.

### Test count estimate

~20 new tests. Total suite stays comfortably fast (<5 min).

## Setup / Migration

### One-time user setup

1. Register a Spotify Developer app at developer.spotify.com (free, ~5 min).
2. Add `http://localhost:8888/callback` to the app's "Redirect URIs" in Settings.
3. Add `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to `.env` (Client Secret comes from the same dashboard).
4. Optional: `SPOTIFY_REDIRECT_URI` defaults to `http://localhost:8888/callback`. Override only if needed.
5. Run `python scripts/spotify_login.py` once. Browser auth flow, ~30 seconds.
6. On the Pi: `sudo apt install librespot`. Optionally enable as a systemd user service.
7. Open phone Spotify → tap Connect icon → tap Pi to pair librespot once.
8. Restart MiniClaw.

### Migration impact

- Existing SoundCloud users: no action required. SoundCloud skill keeps working. Dispatch patterns for transport now route to music-control, but music-control delegates to mpv when SoundCloud is the active source — same observable behavior.
- Adding a third backend later (local files, internet radio, etc.): purely additive. New skill, register an active-source handler in `_stop_all_music()` and `music-control`'s dispatch table, done. No changes to existing skills.

## Open Questions

None. All design choices closed during brainstorming on 2026-05-09.

## Self-Review Notes

- Placeholder scan: clean — no TBD/TODO/FIXME/XXX or empty bullet markers.
- Internal consistency: `_stop_spotify_playback` defined in Components, referenced by `_stop_all_music` in same section; dispatch-pattern repointing in Components 6 lines up with the music-control flow shown in Data Flow D; soundcloud trigger-word documentation in Components 1 lines up with Data Flow E. No contradictions found.
- Scope check: single focused project — three new/edited skills + one shared state field + one auth helper. Fits one implementation plan. No decomposition needed.
- Ambiguity check: tightened the playlist fuzzy-match algorithm (was just "fuzzy_match"; now an explicit four-step ladder) since Mason will want to know exactly when a playlist match wins vs returns the not-found message. Soundcloud transport actions staying in code "for backward compat" is intentional under-specification — they keep working if explicitly invoked, but TierRouter's repointing means they aren't reached via dispatch patterns; harmless either way.
- No CI for the live Spotify path is called out as deliberate (matches prior project posture for Hailo / dashboard).
