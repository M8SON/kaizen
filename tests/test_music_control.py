"""Tests for ContainerManager._active_music_source and _stop_all_music."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_manager():
    from core.container_manager import ContainerManager
    return ContainerManager()


class ActiveMusicSourceState(unittest.TestCase):
    def test_initial_state_is_none(self):
        m = _make_manager()
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_is_idempotent_when_nothing_playing(self):
        m = _make_manager()
        m._stop_all_music()  # must not raise
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_clears_active_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        with patch.object(m, "_stop_mpv"), \
             patch.object(m, "_stop_spotify_playback"):
            m._stop_all_music()
        self.assertIsNone(m._active_music_source)

    def test_stop_all_music_calls_both_backends(self):
        m = _make_manager()
        with patch.object(m, "_stop_mpv") as mock_mpv, \
             patch.object(m, "_stop_spotify_playback") as mock_spot:
            m._stop_all_music()
        mock_mpv.assert_called_once()
        mock_spot.assert_called_once()

    def test_stop_all_music_swallows_backend_exceptions(self):
        m = _make_manager()
        with patch.object(m, "_stop_mpv", side_effect=RuntimeError("boom")), \
             patch.object(m, "_stop_spotify_playback", side_effect=RuntimeError("boom2")):
            m._stop_all_music()  # must not raise
        self.assertIsNone(m._active_music_source)


class MusicControlDispatch(unittest.TestCase):
    def test_no_active_source_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")

    def test_unknown_action_is_rejected(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        result = m._execute_music_control({"action": "explode"})
        self.assertIn("unknown", result.lower())

    def test_soundcloud_skip_calls_mpv_playlist_next(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Skipped.") as mock_act:
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Skipped.")
        mock_act.assert_called_once_with(["playlist-next"], "Skipped.")

    def test_soundcloud_stop_calls_stop_mpv(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_stop_mpv", return_value="Stopped.") as mock_stop:
            result = m._execute_music_control({"action": "stop"})
        self.assertEqual(result, "Stopped.")
        mock_stop.assert_called_once()

    def test_soundcloud_pause_calls_mpv_set_property(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Paused.") as mock_act:
            m._execute_music_control({"action": "pause"})
        mock_act.assert_called_once_with(["set_property", "pause", True], "Paused.")

    def test_soundcloud_resume_calls_mpv_unpause(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Resumed.") as mock_act:
            m._execute_music_control({"action": "resume"})
        mock_act.assert_called_once_with(["set_property", "pause", False], "Resumed.")

    def test_soundcloud_volume_up_calls_mpv_add(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Volume up.") as mock_act:
            m._execute_music_control({"action": "volume_up"})
        mock_act.assert_called_once_with(["add", "volume", 5], "Volume up.")

    def test_soundcloud_volume_down_calls_mpv_add_negative(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        with patch.object(m, "_mpv_action_or_idle", return_value="Volume down.") as mock_act:
            m._execute_music_control({"action": "volume_down"})
        mock_act.assert_called_once_with(["add", "volume", -5], "Volume down.")

    def test_spotify_active_dispatches_to_spotify_branch(self):
        """Spotify branch is exercised in test_spotify_skill once spotipy is wired.
        Here we just verify the handler routes to the right method."""
        m = _make_manager()
        m._active_music_source = "spotify"
        with patch.object(m, "_music_control_spotify", return_value="Skipped.") as mock_sp:
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Skipped.")
        mock_sp.assert_called_once_with("skip")


class MusicControlSkillRegistration(unittest.TestCase):
    def test_native_handler_registered(self):
        m = _make_manager()
        self.assertIn("music-control", m._native_handlers)
        handler = m._native_handlers["music-control"]
        # Verify it's the execute_music_control method by calling it
        result = handler({"action": "skip"})
        # If nothing is playing, should get "Nothing is playing." (the default case)
        self.assertEqual(result, "Nothing is playing.")

    def test_skill_loads_via_skill_loader(self):
        from core.skill_loader import SkillLoader
        loader = SkillLoader()
        skills = loader.load_all()
        self.assertIn("music-control", skills)
        s = skills["music-control"]
        self.assertEqual(s.execution_config.get("type"), "native")


class StopSpotifyPlayback(unittest.TestCase):
    def test_no_op_when_active_source_is_not_spotify(self):
        m = _make_manager()
        m._active_music_source = "soundcloud"
        # Should not even try to call get_spotify_client
        with patch("core.spotify_auth.get_spotify_client") as mock_get:
            m._stop_spotify_playback()
        mock_get.assert_not_called()

    def test_pauses_active_device_when_spotify_is_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            m._stop_spotify_playback()
        sp.pause_playback.assert_called_once_with(device_id="dev1")

    def test_swallows_auth_missing_silently(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        from core.spotify_auth import SpotifyAuthMissing
        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("missing")):
            m._stop_spotify_playback()  # must not raise

    def test_swallows_pause_errors_silently(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1"}]}
        sp.pause_playback.side_effect = RuntimeError("network")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            m._stop_spotify_playback()  # must not raise


class MusicControlSpotifyBranch(unittest.TestCase):
    def test_skip_calls_spotify_next_track(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        sp.next_track.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Skipped.")

    def test_pause_calls_spotify_pause_playback(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "pause"})
        sp.pause_playback.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Paused.")

    def test_resume_calls_spotify_start_playback_no_uris(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "resume"})
        sp.start_playback.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Resumed.")

    def test_volume_up_clamps_to_100(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True, "volume_percent": 98}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "volume_up"})
        sp.volume.assert_called_once_with(volume_percent=100, device_id="dev1")
        self.assertEqual(result, "Volume up.")

    def test_volume_down_clamps_to_0(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True, "volume_percent": 2}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "volume_down"})
        sp.volume.assert_called_once_with(volume_percent=0, device_id="dev1")
        self.assertEqual(result, "Volume down.")

    def test_stop_uses_pause_then_clears_active_source(self):
        m = _make_manager()
        m._active_music_source = "spotify"
        sp = MagicMock()
        sp.devices.return_value = {"devices": [{"id": "dev1", "is_active": True}]}
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "stop"})
        sp.pause_playback.assert_called_once_with(device_id="dev1")
        self.assertIsNone(m._active_music_source)
        self.assertEqual(result, "Stopped.")


class MusicControlExternalSpotifyPlayback(unittest.TestCase):
    """Phone-initiated Spotify Connect playback should route via _execute_music_control."""

    def _sp_with_playback(self, device_id, is_playing=True):
        """Build a mock spotify client whose current_playback() reports the given device."""
        sp = MagicMock()
        sp.current_playback.return_value = {
            "is_playing": is_playing,
            "device": {"id": device_id},
        }
        sp.devices.return_value = {
            "devices": [{"id": device_id, "is_active": True, "volume_percent": 50}]
        }
        return sp

    def test_phone_initiated_playback_on_pinned_device_routes_to_spotify(self):
        m = _make_manager()
        m._active_music_source = None  # phone started it, Kaizen didn't track it
        sp = self._sp_with_playback("dev1")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "volume_down"})
        sp.volume.assert_called_once_with(volume_percent=45, device_id="dev1")
        self.assertEqual(result, "Volume down.")

    def test_playback_on_different_device_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = self._sp_with_playback("phone-speaker-id")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "pause"})
        self.assertEqual(result, "Nothing is playing.")
        sp.pause_playback.assert_not_called()

    def test_no_playback_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = MagicMock()
        sp.current_playback.return_value = None
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")

    def test_paused_playback_on_pinned_device_routes_to_spotify_for_resume(self):
        # When phone playback is paused but the track is still loaded on the
        # pinned device, sp.current_playback() returns the context with
        # is_playing=False. We must still route to spotify so resume works.
        m = _make_manager()
        m._active_music_source = None
        sp = self._sp_with_playback("dev1", is_playing=False)
        with patch("core.spotify_auth.get_spotify_client", return_value=sp), \
             patch.object(m, "_spotify_device_id", return_value="dev1"):
            result = m._execute_music_control({"action": "resume"})
        sp.start_playback.assert_called_once_with(device_id="dev1")
        self.assertEqual(result, "Resumed.")

    def test_spotify_auth_missing_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        from core.spotify_auth import SpotifyAuthMissing
        with patch("core.spotify_auth.get_spotify_client",
                   side_effect=SpotifyAuthMissing("missing")):
            result = m._execute_music_control({"action": "pause"})
        self.assertEqual(result, "Nothing is playing.")

    def test_spotify_api_exception_returns_nothing_playing(self):
        m = _make_manager()
        m._active_music_source = None
        sp = MagicMock()
        sp.current_playback.side_effect = RuntimeError("network blip")
        with patch("core.spotify_auth.get_spotify_client", return_value=sp):
            result = m._execute_music_control({"action": "skip"})
        self.assertEqual(result, "Nothing is playing.")


class MusicControlAckSuccessConstant(unittest.TestCase):
    """Pin the exact set of music-control results that should trigger an ack chime
    instead of TTS. If _music_control_* wording changes, this test fails and
    forces a deliberate decision."""

    def test_constant_contains_exactly_six_known_success_strings(self):
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        self.assertEqual(
            MUSIC_CONTROL_ACK_SUCCESS,
            frozenset({
                "Paused.",
                "Resumed.",
                "Skipped.",
                "Volume up.",
                "Volume down.",
                "Stopped.",
            }),
        )

    def test_constant_matches_soundcloud_success_strings(self):
        """The strings _music_control_soundcloud returns on the happy path
        must all be in the ack set."""
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        soundcloud_successes = {
            "Paused.", "Resumed.", "Skipped.",
            "Volume up.", "Volume down.",
        }
        self.assertTrue(soundcloud_successes.issubset(MUSIC_CONTROL_ACK_SUCCESS))

    def test_constant_matches_spotify_success_strings(self):
        from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
        spotify_successes = {
            "Paused.", "Resumed.", "Skipped.",
            "Volume up.", "Volume down.",
            "Stopped.",
        }
        self.assertTrue(spotify_successes.issubset(MUSIC_CONTROL_ACK_SUCCESS))


if __name__ == "__main__":
    unittest.main()
