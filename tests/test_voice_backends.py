import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_cpu_for_both_paths_when_hailo_runtime_unavailable(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback (wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        side_effect=RuntimeError("transcription self-check failed"),
        create=True,
    )
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_wake_but_cpu_transcription_when_only_wake_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=cpu:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        return_value=None,
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_self_check",
        side_effect=RuntimeError("wake self-check failed"),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_cpu_wake_but_hailo_transcription_when_only_transcription_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        return_value=None,
        create=True,
    )
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_for_both_paths_when_both_are_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_wake_variant_unsupported_for_hailo_falls_back_only_for_wake(
        self, mock_runtime, mock_hybrid_backend
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        with patch(
            "core.voice_backends.hailo_transcription_assets_available",
            return_value=(True, ""),
            create=True,
        ), patch(
            "core.voice_backends.hailo_transcription_self_check",
            return_value=None,
            create=True,
        ):
            backend, message = voice_backends.build_stt_backend("small", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:small, transcription=hailo:base)",
        )

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".miniclaw" / "models" / "hailo-whisper",
        )


class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.HailoWakeRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_hailo_wake_runtime_is_used_when_enabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        mock_load_model.return_value = wake_model

        wake_runtime = mock_wake_runtime_cls.return_value
        wake_runtime.transcribe_wake_audio.return_value = "computer"

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=True,
            use_hailo_transcription=False,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_runtime.transcribe_wake_audio.assert_called_once()
        mock_load_model.assert_called_once_with("base")

    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.HailoWakeRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_cpu_wake_path_is_used_when_hailo_wake_disabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        wake_model.transcribe.return_value = {"text": "Computer"}
        mock_load_model.return_value = wake_model

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=False,
            use_hailo_transcription=True,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_model.transcribe.assert_called_once()
        mock_wake_runtime_cls.assert_not_called()


class HailoRuntimeAssetTests(unittest.TestCase):
    def test_self_check_rejects_missing_model_dir(self):
        from core.hailo_whisper_runtime import HailoTranscriptionRuntime

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "transcription model asset missing"):
                HailoTranscriptionRuntime.self_check(
                    model_name="base",
                    assets_root=Path(tmp),
                )

    def test_self_check_rejects_missing_hailo_platform(self):
        from core.hailo_whisper_runtime import HailoTranscriptionRuntime

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base" / "decoder_assets").mkdir(parents=True)
            (root / "base" / "hefs" / "hailo8").mkdir(parents=True)
            (root / "base" / "decoder_assets" / "token_embedding_weight_base.npy").touch()
            (root / "base" / "decoder_assets" / "onnx_add_input_base.npy").touch()
            (root / "base" / "hefs" / "hailo8" / "base-whisper-encoder-5s.hef").touch()
            (root / "base" / "hefs" / "hailo8" / "base-whisper-decoder-fixed-sequence-matmul-split.hef").touch()

            with patch(
                "core.hailo_whisper_runtime._hailo_platform_import_error",
                ModuleNotFoundError("no hailo"),
            ):
                with self.assertRaisesRegex(RuntimeError, "hailo_platform python module not installed"):
                    HailoTranscriptionRuntime.self_check(
                        model_name="base",
                        assets_root=root,
                    )


class HailoWakeRuntimeAssetTests(unittest.TestCase):
    def test_wake_self_check_rejects_missing_model_dir(self):
        from core.hailo_whisper_runtime import HailoWakeRuntime

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "wake model asset missing"):
                HailoWakeRuntime.self_check(
                    model_name="tiny",
                    assets_root=Path(tmp),
                )


class WhisperWakeBackendTests(unittest.TestCase):
    @patch("core.voice_backends.whisper.load_model")
    def test_detect_returns_true_when_phrase_present(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "hello computer hi"}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        audio = np.zeros(16000, dtype=np.float32)

        self.assertTrue(backend.detect(audio))

    @patch("core.voice_backends.whisper.load_model")
    def test_detect_returns_false_when_phrase_absent(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "hello world"}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        audio = np.zeros(16000, dtype=np.float32)

        self.assertFalse(backend.detect(audio))

    @patch("core.voice_backends.whisper.load_model")
    def test_detect_normalises_case_and_whitespace(self, mock_load):
        model = MagicMock()
        model.transcribe.return_value = {"text": "  Computer.  "}
        mock_load.return_value = model

        backend = voice_backends.WhisperWakeBackend(
            model_name="tiny", wake_phrase="computer"
        )
        self.assertTrue(backend.detect(np.zeros(16000, dtype=np.float32)))


class OpenWakeWordBackendTests(unittest.TestCase):
    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_true_when_score_exceeds_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.85}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        audio = np.zeros(1280, dtype=np.float32)

        self.assertTrue(backend.detect(audio))
        mock_owww.Model.assert_called_once_with(
            wakeword_model_paths=["/fake/hey_jarvis_v0.1.onnx"]
        )

    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_false_when_score_below_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.3}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        self.assertFalse(backend.detect(np.zeros(1280, dtype=np.float32)))

    @patch("core.voice_backends.openwakeword")
    def test_init_raises_for_unknown_model_name(self, mock_owww):
        mock_owww.models = {"hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}}
        with self.assertRaises(ValueError):
            voice_backends.OpenWakeWordBackend(
                model_name="not_a_real_model", threshold=0.5
            )

    @patch("core.voice_backends.openwakeword")
    def test_reset_clears_model_state(self, mock_owww):
        mock_model = MagicMock()
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        backend.reset()

        mock_model.reset.assert_called_once()

    @patch("core.voice_backends._OPENWAKEWORD_AVAILABLE", False)
    def test_init_raises_when_openwakeword_unavailable(self):
        with self.assertRaises(ImportError):
            voice_backends.OpenWakeWordBackend(
                model_name="hey_jarvis", threshold=0.5
            )

    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_true_when_score_equals_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.5}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )

        self.assertTrue(backend.detect(np.zeros(1280, dtype=np.float32)))


class BuildWakeBackendTests(unittest.TestCase):
    @patch("core.voice_backends.OpenWakeWordBackend")
    def test_returns_openwakeword_when_backend_is_openwakeword(self, mock_owww):
        instance = object()
        mock_owww.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="openwakeword",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("openwakeword", message)
        self.assertIn("hey_jarvis", message)

    @patch("core.voice_backends.WhisperWakeBackend")
    def test_returns_whisper_when_backend_is_whisper(self, mock_whisper):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="whisper",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("whisper", message)
        self.assertIn("computer", message)

    @patch("core.voice_backends.WhisperWakeBackend")
    @patch(
        "core.voice_backends.OpenWakeWordBackend",
        side_effect=ImportError("openwakeword not installed"),
    )
    def test_falls_back_to_whisper_on_openwakeword_failure(
        self, mock_owww, mock_whisper
    ):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            backend_name="openwakeword",
            model_name="hey_jarvis",
            threshold=0.5,
            wake_phrase="computer",
            whisper_model="tiny",
        )

        self.assertIs(backend, instance)
        self.assertIn("fallback", message.lower())

    def test_raises_for_unknown_backend_name(self):
        with self.assertRaises(ValueError):
            voice_backends.build_wake_backend(
                backend_name="oepnwakeword",  # typo
                model_name="hey_jarvis",
                threshold=0.5,
                wake_phrase="computer",
                whisper_model="tiny",
            )


class RmsVadBackendTests(unittest.TestCase):
    def test_is_speech_true_when_amplitude_above_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        loud = (np.ones(1024, dtype=np.int16) * 5000).astype(np.int16)
        self.assertTrue(backend.is_speech(loud))

    def test_is_speech_false_when_amplitude_below_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        quiet = np.zeros(1024, dtype=np.int16)
        self.assertFalse(backend.is_speech(quiet))

    def test_reset_is_noop(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        backend.reset()  # should not raise

    def test_is_speech_handles_float32_normalized_audio(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        # Normalized float32 audio at amplitude ~0.15 ≈ int16 5000 — well above
        # threshold 1000. Without proper rescaling this would astype(int16)
        # truncate to all zeros and incorrectly return False.
        loud_float = np.ones(1024, dtype=np.float32) * 0.15
        self.assertTrue(backend.is_speech(loud_float))


class SileroVadBackendTests(unittest.TestCase):
    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_true_when_score_above_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.85)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x  # passthrough

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # 1024 samples → exactly two 512-sample sub-frames consumed
        audio = np.zeros(1024, dtype=np.float32)

        self.assertTrue(backend.is_speech(audio))
        self.assertEqual(mock_model.call_count, 2)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_false_when_score_below_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.2)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        self.assertFalse(backend.is_speech(np.zeros(1024, dtype=np.float32)))

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_carry_over_short_chunks(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.0)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # Two 300-sample chunks: first call has no full 512-frame, second does
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 1)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_reset_clears_buffer_and_model_state(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        backend.is_speech(np.zeros(300, dtype=np.float32))  # leaves 300 samples in buffer
        backend.reset()
        # After reset, a 200-sample chunk should not yet trigger the model (buffer cleared)
        backend.is_speech(np.zeros(200, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        mock_model.reset_states.assert_called_once()


if __name__ == "__main__":
    unittest.main()
