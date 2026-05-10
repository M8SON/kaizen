# music-control fallback for phone-initiated Spotify playback

**Date:** 2026-05-10
**Status:** Approved, pending implementation plan
**Owner:** Mason

## Goal

When the user starts Spotify playback from their phone via Connect (tapping "MiniClaw" in the Spotify device picker) and then asks MiniClaw to control it ("turn the volume down", "pause", "skip"), MiniClaw should route the transport command to Spotify instead of returning "Nothing is playing."

## Background

`ContainerManager._active_music_source` only tracks playback the orchestrator initiated (via the `spotify` or `soundcloud` skills). Phone-initiated Spotify Connect playback hits the gate in `_execute_music_control` (`core/container_manager.py:846-848`):

```python
source = self._active_music_source
if source is None:
    return "Nothing is playing."
```

This early return is the bug — at this point, MiniClaw could ask Spotify whether *something* is playing on the pinned device.

## Non-goals

- Detecting playback that's not on the pinned MiniClaw device (e.g. user playing on phone speaker). Out of scope — they didn't ask MiniClaw to control phone audio.
- Persisting `_active_music_source = "spotify"` after a successful probe. We re-probe each call (~200–500ms cost) so MiniClaw stays accurate when the user pauses from their phone mid-session.
- A SoundCloud equivalent. SoundCloud playback can't start without MiniClaw initiating it, so the gate already covers SoundCloud correctly.
- Generalizing the probe beyond `_execute_music_control`. No other code path needs it today.

## Design

Add a helper:

```python
def _detect_external_spotify_playback(self) -> str | None:
    """Probe Spotify for active playback on the pinned MiniClaw device.

    Returns 'spotify' if the user has phone-initiated playback running on
    the device this MiniClaw owns. Returns None if Spotify isn't set up,
    no playback is active, or playback is on a different device.
    """
    from core.spotify_auth import get_spotify_client, SpotifyAuthMissing
    try:
        sp = get_spotify_client()
    except SpotifyAuthMissing:
        return None
    try:
        playback = sp.current_playback()
        if not playback or not playback.get("is_playing"):
            return None
        playback_device_id = (playback.get("device") or {}).get("id")
        pinned_device_id = self._spotify_device_id(sp)
        if playback_device_id and playback_device_id == pinned_device_id:
            return "spotify"
    except Exception:
        logger.exception("_detect_external_spotify_playback failed")
    return None
```

Update the gate:

```python
def _execute_music_control(self, tool_input: dict) -> str:
    action = str(tool_input.get("action") or "").strip().lower()
    if action not in self._MUSIC_CONTROL_ACTIONS:
        return f"Unknown music-control action: {action!r}"

    source = self._active_music_source or self._detect_external_spotify_playback()
    if source is None:
        return "Nothing is playing."

    if source == "soundcloud":
        return self._music_control_soundcloud(action)
    if source == "spotify":
        return self._music_control_spotify(action)
    return f"Unknown active music source: {source!r}"
```

That's the entire production-code change. Two diff locations: one new helper, one or-fallback line in the existing gate.

## Failure modes

| Failure | Handled by |
|---------|------------|
| Spotify creds not configured | `SpotifyAuthMissing` → returns None → gate returns "Nothing is playing." (existing message) |
| Spotify API call raises | `except Exception` → logged → returns None → gate returns "Nothing is playing." |
| `current_playback()` returns None | Returns None → gate returns "Nothing is playing." |
| Playback active but on phone (not pinned device) | Device-id mismatch → returns None → gate returns "Nothing is playing." (correct: we don't own that audio) |
| Playback active on pinned device | Device-id match → returns "spotify" → routes to `_music_control_spotify(action)` |

## Testing

Add tests to `tests/test_music_control.py`. Following the existing `MusicControlSpotifyBranch` pattern of mocking `get_spotify_client`:

1. **Phone-initiated playback on pinned device** — `current_playback()` returns matching device.id → transport routes to spotify branch.
2. **Phone-initiated playback on a different device** — `current_playback()` returns a different device.id → returns "Nothing is playing."
3. **No playback at all** — `current_playback()` returns None → returns "Nothing is playing."
4. **`is_playing: false`** — `current_playback()` returns a paused session → returns "Nothing is playing."
5. **`SpotifyAuthMissing`** — auth helper raises → returns "Nothing is playing." (no crash, no traceback).
6. **Spotify API raises** — `current_playback()` raises RuntimeError → returns "Nothing is playing." (graceful).

The existing `test_no_active_source_returns_nothing_playing` test will keep passing because `get_spotify_client()` raises `SpotifyAuthMissing` in test envs without creds — the new code path returns None and the gate behaves as before.

## Open questions

None.

## Risks

- **Latency cost on every "no active source" call.** ~200–500ms for the Spotify API round-trip. Only paid when `_active_music_source` is None — so MiniClaw-initiated playback (the common path) isn't affected.
- **Spotify API rate limits.** spotipy uses Spotify's app credentials; per-user rate limits are generous. A user spamming "turn it up" 50 times in a minute would still be well under limits.
- **OAuth scope check.** Verified: `user-read-playback-state` is already in `core/spotify_auth.py:24-30`. No re-auth required.
