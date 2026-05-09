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


if __name__ == "__main__":
    unittest.main()
