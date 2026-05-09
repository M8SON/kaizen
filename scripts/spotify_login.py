#!/usr/bin/env python3
"""One-time browser-based OAuth dance to authorize MiniClaw with Spotify.

Setup steps:
  1. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env (or export
     them before running this script).
  2. Make sure http://localhost:8888/callback is registered as a Redirect
     URI in your Spotify dev app settings.
  3. Run: python scripts/spotify_login.py
  4. Open the printed URL in any browser, approve consent.
  5. Your browser will redirect to a localhost URL that may show a
     "connection refused" error — that's expected. Copy the FULL URL
     (including ?code=…) and paste it back when prompted.

After that, the refresh token is cached at ~/.miniclaw/spotify-tokens.json
and MiniClaw uses it forever (or until you revoke the app at
https://www.spotify.com/account/apps/).
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

# Load .env so SPOTIFY_* env vars are picked up
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional for this script; env vars can be exported manually

# Make core/ importable when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.spotify_auth import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    SCOPES,
    TOKEN_CACHE_PATH,
)


def main() -> int:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    if not client_id or not client_secret:
        print(
            "ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in "
            ".env (or exported in the shell) before running this script.",
            file=sys.stderr,
        )
        return 1

    from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_handler = CacheFileHandler(cache_path=str(TOKEN_CACHE_PATH))
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=cache_handler,
        open_browser=False,
    )

    print("\n=== Spotify OAuth setup ===\n")
    print("1. Open this URL in your browser:\n")
    print(f"   {oauth.get_authorize_url()}\n")
    print("2. Approve consent. Your browser will redirect to a localhost URL")
    print(f"   ({redirect_uri}?code=...). It may show a connection error — that's fine.\n")
    print("3. Paste the FULL redirected URL here (it has ?code=... in the query string):\n")

    try:
        response_url = input("Redirected URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(response_url)
    code_values = urllib.parse.parse_qs(parsed.query).get("code")
    if not code_values:
        print(
            "\nERROR: couldn't find ?code=... in the URL you pasted. Make sure "
            "you copy the URL after the redirect, not before.",
            file=sys.stderr,
        )
        return 1
    code = code_values[0]

    token_info = oauth.get_access_token(code, as_dict=True, check_cache=False)
    print(f"\n✓ Token cached at {TOKEN_CACHE_PATH}")
    print(f"  Access token expires in {token_info.get('expires_in', '?')} seconds.")
    print("  Refresh token saved (auto-refreshed forever).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
