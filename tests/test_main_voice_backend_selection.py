import unittest
import unittest.mock
from unittest.mock import patch

import main


class BuildVoiceInterfaceSelectionTests(unittest.TestCase):
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_selected_backend(
        self, mock_build_stt_backend, mock_voice_interface
    ):
        fake_backend = object()
        mock_build_stt_backend.return_value = (
            fake_backend,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)",
        )

        main.build_voice_interface()

        _, kwargs = mock_voice_interface.call_args
        self.assertIs(kwargs["stt_backend"], fake_backend)

    @patch("builtins.print")
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_prints_backend_status_once(
        self, mock_build_stt_backend, mock_voice_interface, mock_print
    ):
        fake_backend = object()
        message = (
            "STT backend: CPU Whisper fallback "
            "(wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable"
        )
        mock_build_stt_backend.return_value = (fake_backend, message)

        main.build_voice_interface()

        mock_print.assert_called_once_with(message)


class VoiceWakeBackendIntegrationTests(unittest.TestCase):
    @patch("core.voice.pyaudio.PyAudio")
    @patch("core.voice.resolve_input_device", return_value=None)
    @patch("core.voice.resolve_output_device", return_value=None)
    @patch("core.voice.output_samplerate", return_value=48000)
    def test_wait_for_wake_word_uses_wake_backend_when_provided(
        self, _mock_sr, _mock_out, _mock_in, mock_pa
    ):
        from core.voice import VoiceInterface

        wake_backend = unittest.mock.MagicMock()
        # Simulate: silence, silence, wake. The exact number of False returns
        # depends on how often wait_for_wake_word evaluates the buffer; allow
        # any prefix of False before the True.
        wake_backend.detect.side_effect = [False, False, True]
        stt_backend = unittest.mock.MagicMock()
        tts_backend = unittest.mock.MagicMock()

        mock_stream = unittest.mock.MagicMock()
        # 1s of silence at 16kHz mono int16 per read
        mock_stream.read.return_value = b"\x00" * 32000
        mock_pa.return_value.open.return_value = mock_stream

        voice = VoiceInterface(
            stt_backend=stt_backend,
            wake_backend=wake_backend,
            tts_backend=tts_backend,
            enable_tts=False,
        )

        result = voice.wait_for_wake_word()

        self.assertTrue(result)
        # Some number of calls before True; at least one
        self.assertGreaterEqual(wake_backend.detect.call_count, 1)
        # Critically: stt_backend.transcribe_wake_audio is never called
        # when wake_backend is provided.
        stt_backend.transcribe_wake_audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
