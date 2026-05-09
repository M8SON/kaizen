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


if __name__ == "__main__":
    unittest.main()
