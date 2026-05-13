"""Spotify OAuth token cache + client factory.

Uses spotipy's SpotifyOAuth with a file-backed cache handler so the
refresh token (set up once via scripts/spotify_login.py) is read on
every call and auto-refreshed by spotipy as needed.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_CACHE_PATH = Path.home() / ".kaizen" / "spotify-tokens.json"

DEFAULT_REDIRECT_URI = "http://localhost:8888/callback"

# Minimum scopes the spotify skill needs:
#   user-modify-playback-state — start/pause/skip/queue
#   user-read-playback-state   — read current device + status
#   user-read-private          — required by some endpoints
#   playlist-read-private      — list user's saved playlists
#   user-library-read          — read liked tracks (used by play_playlist fallback)
SCOPES = " ".join([
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-private",
    "playlist-read-private",
    "user-library-read",
])


class SpotifyAuthMissing(Exception):
    """Raised when env vars or token cache aren't set up yet.

    Callers should catch this and surface the message as a friendly tool
    result — Claude reads it back to the user as a setup hint."""


def get_spotify_client():
    """Return a spotipy.Spotify instance using the cached refresh token.

    Raises SpotifyAuthMissing if env vars or the token file are missing.
    """
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    if not client_id or not client_secret:
        raise SpotifyAuthMissing(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env"
        )
    if not TOKEN_CACHE_PATH.exists():
        raise SpotifyAuthMissing(
            f"Spotify token cache not found at {TOKEN_CACHE_PATH}. "
            "Run scripts/spotify_login.py once to authorize."
        )

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth, CacheFileHandler

    cache_handler = CacheFileHandler(cache_path=str(TOKEN_CACHE_PATH))
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler,
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=oauth)
