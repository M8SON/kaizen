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


if __name__ == "__main__":
    unittest.main()
