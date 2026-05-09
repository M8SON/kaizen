"""Tests for core.spotify_auth — token cache + client factory."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class GetSpotifyClientErrorPaths(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_raises_when_client_id_missing(self):
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing

        with self.assertRaises(SpotifyAuthMissing) as ctx:
            get_spotify_client()
        self.assertIn("SPOTIFY_CLIENT_ID", str(ctx.exception))

    @patch.dict("os.environ", {"SPOTIFY_CLIENT_ID": "x", "SPOTIFY_CLIENT_SECRET": "y"}, clear=True)
    def test_raises_when_token_cache_missing(self):
        from core.spotify_auth import get_spotify_client, SpotifyAuthMissing, TOKEN_CACHE_PATH

        with patch.object(Path, "exists", return_value=False):
            with self.assertRaises(SpotifyAuthMissing) as ctx:
                get_spotify_client()
        self.assertIn("scripts/spotify_login.py", str(ctx.exception))


class GetSpotifyClientHappyPath(unittest.TestCase):
    @patch.dict("os.environ", {
        "SPOTIFY_CLIENT_ID": "abc",
        "SPOTIFY_CLIENT_SECRET": "def",
        "SPOTIFY_REDIRECT_URI": "http://localhost:8888/callback",
    }, clear=True)
    def test_returns_spotipy_client_when_all_set(self):
        from core import spotify_auth

        with patch.object(Path, "exists", return_value=True), \
             patch("spotipy.oauth2.SpotifyOAuth") as mock_oauth_cls, \
             patch("spotipy.Spotify") as mock_spotify_cls:
            mock_oauth = MagicMock()
            mock_oauth_cls.return_value = mock_oauth
            mock_client = MagicMock()
            mock_spotify_cls.return_value = mock_client

            result = spotify_auth.get_spotify_client()

        self.assertIs(result, mock_client)
        # OAuth is constructed with the configured creds + scopes
        kwargs = mock_oauth_cls.call_args.kwargs
        self.assertEqual(kwargs["client_id"], "abc")
        self.assertEqual(kwargs["client_secret"], "def")
        self.assertEqual(kwargs["redirect_uri"], "http://localhost:8888/callback")
        self.assertIn("user-modify-playback-state", kwargs["scope"])
        # spotipy.Spotify is wired to the OAuth manager
        mock_spotify_cls.assert_called_once_with(auth_manager=mock_oauth)


if __name__ == "__main__":
    unittest.main()
