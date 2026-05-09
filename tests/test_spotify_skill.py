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


class SpotifyPlayPlaylist(unittest.TestCase):
    def test_no_name_returns_friendly_error(self):
        m = _make_manager()
        result = m._execute_spotify({"action": "play_playlist", "name": ""})
        self.assertIn("no playlist name", result.lower())

    def test_play_playlist_exact_match(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {"name": "COUNTRY", "uri": "spotify:playlist:c1"},
                {"name": "HIPHOP", "uri": "spotify:playlist:h1"},
            ],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_stop_all_music") as mock_stop:
            result = m._execute_spotify({"action": "play_playlist", "name": "COUNTRY"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:c1"
        )
        mock_stop.assert_called_once()
        self.assertEqual(m._active_music_source, "spotify")
        self.assertIn("COUNTRY", result)

    def test_play_playlist_case_insensitive(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"name": "COUNTRY", "uri": "spotify:playlist:c1"}],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "country"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:c1"
        )

    def test_play_playlist_substring_match(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [
                {"name": "Workout 2026", "uri": "spotify:playlist:w1"},
                {"name": "Dinner Vibes", "uri": "spotify:playlist:d1"},
            ],
            "next": None,
        }
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "workout"})

        sp.start_playback.assert_called_once_with(
            device_id="dev1", context_uri="spotify:playlist:w1"
        )

    def test_play_playlist_no_match_returns_message(self):
        m = _make_manager()
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [{"name": "JAZZ", "uri": "spotify:playlist:j1"}],
            "next": None,
        }

        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_spotify({"action": "play_playlist", "name": "metal"})

        self.assertIn("Couldn't find", result)
        sp.start_playback.assert_not_called()
        self.assertIsNone(m._active_music_source)


class FuzzyMatchPlaylistHelper(unittest.TestCase):
    def test_exact_case_insensitive(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertEqual(_fuzzy_match_playlist("country", ["COUNTRY", "JAZZ"]), "COUNTRY")

    def test_substring_either_direction(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertEqual(
            _fuzzy_match_playlist("workout", ["Workout 2026", "Dinner"]),
            "Workout 2026",
        )

    def test_levenshtein_fallback(self):
        from core.container_manager import _fuzzy_match_playlist
        # "countrie" -> "COUNTRY" via difflib close-match
        self.assertEqual(_fuzzy_match_playlist("countrie", ["COUNTRY", "POP"]), "COUNTRY")

    def test_no_match_returns_none(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertIsNone(_fuzzy_match_playlist("metal", ["COUNTRY", "POP"]))

    def test_empty_names_returns_none(self):
        from core.container_manager import _fuzzy_match_playlist
        self.assertIsNone(_fuzzy_match_playlist("anything", []))


class SpotifySkillRegistration(unittest.TestCase):
    def test_native_handler_registered(self):
        m = _make_manager()
        self.assertIn("spotify", m._native_handlers)
        self.assertEqual(m._native_handlers["spotify"], m._execute_spotify)

    @patch.dict("os.environ", {
        "SPOTIFY_CLIENT_ID": "x", "SPOTIFY_CLIENT_SECRET": "y",
    }, clear=False)
    def test_skill_loads_when_env_present(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        skills = loader.load_all()
        self.assertIn("spotify", skills)
        s = skills["spotify"]
        self.assertEqual(s.execution_config.get("type"), "native")

    @patch.dict("os.environ", {}, clear=True)
    def test_skill_skipped_when_env_missing(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        loader.load_all()
        self.assertIn("spotify", loader.skipped_skills)
        info = loader.skipped_skills["spotify"]
        self.assertIn("SPOTIFY_CLIENT_ID", " ".join(info.get("missing_env_vars", [])))


class SpotifyDeviceIdSelection(unittest.TestCase):
    """SPOTIFY_DEVICE_NAME, when set, must pin playback to the named device
    even when another device (e.g. FireTV) is currently active. When unset,
    fall back to the historical 'first active, then first available' policy.
    """

    def _devices(self, *items):
        return {"devices": list(items)}

    def _dev(self, name, did, active=False):
        return {"name": name, "id": did, "is_active": active}

    @patch.dict("os.environ", {"SPOTIFY_DEVICE_NAME": "MiniClaw"}, clear=False)
    def test_pinned_device_chosen_over_active_other(self):
        m = _make_manager()
        sp = MagicMock()
        sp.devices.return_value = self._devices(
            self._dev("Amazon FireTV Cube Gen 2", "fire-id", active=True),
            self._dev("MiniClaw", "mini-id", active=False),
        )
        self.assertEqual(m._spotify_device_id(sp), "mini-id")

    @patch.dict("os.environ", {"SPOTIFY_DEVICE_NAME": "MiniClaw"}, clear=False)
    def test_pinned_device_match_is_case_insensitive(self):
        m = _make_manager()
        sp = MagicMock()
        sp.devices.return_value = self._devices(
            self._dev("MINICLAW", "mini-id", active=True),
        )
        self.assertEqual(m._spotify_device_id(sp), "mini-id")

    @patch.dict("os.environ", {"SPOTIFY_DEVICE_NAME": "MiniClaw"}, clear=False)
    def test_pinned_device_missing_returns_none_not_fallback(self):
        m = _make_manager()
        sp = MagicMock()
        sp.devices.return_value = self._devices(
            self._dev("Amazon FireTV Cube Gen 2", "fire-id", active=True),
        )
        # User asked for MiniClaw only — must NOT silently fall back to FireTV.
        self.assertIsNone(m._spotify_device_id(sp))

    @patch.dict("os.environ", {}, clear=True)
    def test_no_pin_falls_back_to_active_first(self):
        m = _make_manager()
        sp = MagicMock()
        sp.devices.return_value = self._devices(
            self._dev("Other", "other-id", active=False),
            self._dev("Active", "active-id", active=True),
        )
        self.assertEqual(m._spotify_device_id(sp), "active-id")

    @patch.dict("os.environ", {}, clear=True)
    def test_no_pin_no_active_returns_first_available(self):
        m = _make_manager()
        sp = MagicMock()
        sp.devices.return_value = self._devices(
            self._dev("First", "first-id", active=False),
            self._dev("Second", "second-id", active=False),
        )
        self.assertEqual(m._spotify_device_id(sp), "first-id")


if __name__ == "__main__":
    unittest.main()
