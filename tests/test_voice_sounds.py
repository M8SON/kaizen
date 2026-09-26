"""Smoke tests for VoiceInterface R2-D2 sound helpers — they must be callable
without raising under both enabled and disabled TTS configurations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class PlayAckSound(unittest.TestCase):
    def test_method_exists_on_voice_interface(self):
        from core.voice import VoiceInterface
        self.assertTrue(hasattr(VoiceInterface, "play_ack_sound"))
        self.assertTrue(callable(VoiceInterface.play_ack_sound))

    def test_no_op_when_tts_disabled(self):
        """When enable_tts is False, the method must return immediately
        without touching sounddevice. We verify by patching sd.play and
        asserting it was never called."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = False
        with patch("core.voice.sd.play") as mock_play:
            v.play_ack_sound()
        mock_play.assert_not_called()

    def test_swallows_audio_errors(self):
        """Audio backend exceptions must be logged and swallowed —
        the voice loop can't crash on a missing speaker."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("core.voice.sd.play", side_effect=RuntimeError("audio gone")):
            v.play_ack_sound()  # must not raise


class PlayThinkingSoundNonBlocking(unittest.TestCase):
    def test_does_not_block_on_sd_wait(self):
        """play_thinking_sound now fires from on_speech_done the instant the
        user stops talking, just before STT runs. It must play asynchronously
        (no sd.wait) so it overlaps the STT/processing wait instead of
        delaying it — matching play_ack_sound / play_response_ready_sound."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("core.voice.sd.play") as mock_play, \
             patch("core.voice.sd.wait") as mock_wait:
            v.play_thinking_sound()
        mock_play.assert_called_once()
        mock_wait.assert_not_called()


class PlayFillerSound(unittest.TestCase):
    def test_no_op_when_tts_disabled(self):
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = False
        with patch("core.voice.sd.play") as mock_play:
            v.play_filler("weather")
        mock_play.assert_not_called()

    def test_no_op_when_category_has_no_cached_audio(self):
        """A category with no cached .npy files must not raise or call
        sd.play — the feature degrades silently until
        scripts/build_filler_audio.py has been run for that category."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("pathlib.Path.is_dir", return_value=False), \
             patch("core.voice.sd.play") as mock_play:
            v.play_filler("nonexistent_category")
        mock_play.assert_not_called()

    def test_plays_cached_audio_when_present(self):
        import numpy as np
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0

        fake_path = Path("/fake/weather/phrase.npy")
        with patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.glob", return_value=[fake_path]), \
             patch("core.voice.np.load", return_value=np.zeros(100, dtype=np.float32)), \
             patch("core.voice.sd.play") as mock_play:
            v.play_filler("weather")
        mock_play.assert_called_once()

    def test_swallows_playback_errors(self):
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("pathlib.Path.is_dir", side_effect=RuntimeError("disk gone")):
            v.play_filler("weather")  # must not raise


if __name__ == "__main__":
    unittest.main()


class PlayAnswerSound(unittest.TestCase):
    def _voice(self):
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 24000
        v._output_device_index = 0
        return v

    def test_false_when_tts_disabled(self):
        v = self._voice()
        v.enable_tts = False
        with patch("core.voice.sd.play") as mock_play:
            self.assertFalse(v.play_answer("identity", "I'm Jarvis."))
        mock_play.assert_not_called()

    def test_false_when_no_cached_audio(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, \
             patch("core.filler_classifier.FILLER_AUDIO_ROOT", Path(tmp)), \
             patch("core.voice.sd.play") as mock_play:
            self.assertFalse(self._voice().play_answer("identity", "I'm Jarvis."))
        mock_play.assert_not_called()

    def test_plays_and_waits_when_cached(self):
        """Answers block until playback ends so the next listen() doesn't
        record the assistant's own voice."""
        import tempfile
        from pathlib import Path
        import numpy as np
        from core.filler_classifier import phrase_slug
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "identity"
            cat.mkdir()
            np.save(cat / f"{phrase_slug('I am Jarvis.')}.npy", np.zeros(2400, dtype=np.float32))
            with patch("core.filler_classifier.FILLER_AUDIO_ROOT", Path(tmp)), \
                 patch("core.voice.sd.play") as mock_play, \
                 patch("core.voice.sd.wait") as mock_wait:
                self.assertTrue(self._voice().play_answer("identity", "I am Jarvis."))
        mock_play.assert_called_once()
        mock_wait.assert_called_once()


class PhraseOutputBuffer(unittest.TestCase):
    def test_filler_and_answer_play_with_large_output_buffer(self):
        """Phrases start while the main thread is CPU-busy; a small output
        buffer underran on the first syllable on the Pi."""
        import tempfile
        from pathlib import Path
        import numpy as np
        from core import voice as voice_mod
        from core.filler_classifier import phrase_slug
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = voice_mod.KOKORO_SAMPLE_RATE
        v._output_device_index = 0
        with tempfile.TemporaryDirectory() as tmp:
            cat = Path(tmp) / "weather"
            cat.mkdir()
            np.save(cat / f"{phrase_slug('Hi.')}.npy", np.ones(100, dtype=np.float32))
            with patch("core.filler_classifier.FILLER_AUDIO_ROOT", Path(tmp)), \
                 patch("pathlib.Path.home", return_value=Path(tmp).parent), \
                 patch("core.voice.sd.play") as mock_play, patch("core.voice.sd.wait"):
                v.play_answer("weather", "Hi.")
        self.assertEqual(mock_play.call_args.kwargs["latency"], voice_mod.PHRASE_OUTPUT_LATENCY_S)
        self.assertGreaterEqual(voice_mod.PHRASE_OUTPUT_LATENCY_S, 0.3)
