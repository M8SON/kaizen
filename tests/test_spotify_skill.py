"""Tests for the spotify native skill handler."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_manager():
    from core.container_manager import ContainerManager
    return ContainerManager()


def _fake_track(name="Cool Song", artist="Some Band", uri="spotify:track:abc"):
    return {
        "name": name,
        "uri": uri,
        "artists": [{"name": artist}],
    }


class SpotifyPlayAction(unittest.TestCase):
    def test_no_query_returns_friendly_error(self):
        m = _make_manager()
        result = m._execute_spotify({"action": "play", "query": ""})
        self.assertIn("no", result.lower())
        self.assertNotIn("Traceback", result)

    def test_auth_missing_surfaces_setup_message(self):
        m = _make_manager()
        from core.spotify_auth import SpotifyAuthMissing

        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("SPOTIFY_CLIENT_ID...")):
            result = m._execute_spotify({"action": "play", "query": "country"})
        self.assertIn("Spotify isn't set up", result)

    def test_play_with_results_starts_playback(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": [_fake_track()]}}
        sp.devices.return_value = {"devices": [
            {"id": "dev1", "name": "Pi librespot", "is_active": True}
        ]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_stop_all_music") as mock_stop:
            result = m._execute_spotify({"action": "play", "query": "cool song"})

        sp.search.assert_called_once()
        mock_stop.assert_called_once()
        sp.start_playback.assert_called_once_with(
            device_id="dev1", uris=["spotify:track:abc"]
        )
        self.assertEqual(m._active_music_source, "spotify")
        self.assertIn("Cool Song", result)
        self.assertIn("Some Band", result)

    def test_play_with_no_results_returns_friendly_message(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": []}}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play", "query": "obscure"})

        self.assertIn("Couldn't find", result)
        sp.start_playback.assert_not_called()
        self.assertIsNone(m._active_music_source)

    def test_play_with_no_active_device_returns_setup_hint(self):
        m = _make_manager()
        sp = MagicMock()
        sp.search.return_value = {"tracks": {"items": [_fake_track()]}}
        sp.devices.return_value = {"devices": []}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play", "query": "x"})

        self.assertIn("librespot", result.lower())
        sp.start_playback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
