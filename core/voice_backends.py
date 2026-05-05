"""
Voice backend implementations for MiniClaw.

These classes isolate concrete STT and TTS providers from the microphone and
conversation control logic in VoiceInterface.
"""

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

import sounddevice as sd
import whisper
from kokoro import KPipeline
from core.audio_devices import resample
from core.hailo_whisper_runtime import HailoTranscriptionRuntime, HailoWakeRuntime

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
HAILO_WHISPER_ASSET_ROOT = Path.home() / ".miniclaw" / "models" / "hailo-whisper"
SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}
SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}


class SttBackend(Protocol):
    def transcribe_wake_audio(self, audio_float) -> str: ...
    def transcribe_file(self, audio_file: str) -> str: ...


class WakeBackend(Protocol):
    """Continuous wake-word detector. Consumes audio chunks, returns trigger bool."""
    def detect(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class WhisperWakeBackend:
    """Fallback wake backend — runs Whisper on a 2s window and substring-matches.

    Preserves current MiniClaw behaviour. Used when WAKE_BACKEND=whisper or when
    openWakeWord fails to load.
    """

    def __init__(self, model_name: str = "tiny", wake_phrase: str = "computer"):
        logger.info("Loading Whisper wake model: %s", model_name)
        self.model = whisper.load_model(model_name)
        self.wake_phrase = wake_phrase.lower().strip()

    def detect(self, audio_chunk: np.ndarray) -> bool:
        result = self.model.transcribe(audio_chunk, language="en", fp16=False)
        transcript = result["text"].lower().strip()
        return self.wake_phrase in transcript

    def reset(self) -> None:
        # Whisper is stateless per call; nothing to reset.
        pass


try:
    import openwakeword
    _OPENWAKEWORD_AVAILABLE = True
except ImportError:
    openwakeword = None  # type: ignore[assignment]
    _OPENWAKEWORD_AVAILABLE = False


class OpenWakeWordBackend:
    """Primary wake backend — purpose-built keyword spotter.

    Expects ~80ms audio chunks at 16kHz int16 or float32. Returns True when
    the model's score for `model_name` crosses `threshold`.

    `model_name` accepts canonical openwakeword names ("hey_jarvis", "alexa",
    "hey_mycroft", "timer", "weather"). The backend resolves to the bundled
    ONNX path and to the version-suffixed score-dict key automatically.
    """

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        if not _OPENWAKEWORD_AVAILABLE:
            raise ImportError("openwakeword not installed")
        if model_name not in openwakeword.models:
            raise ValueError(
                f"unknown openwakeword model {model_name!r}; "
                f"available: {list(openwakeword.models)}"
            )

        logger.info("Loading openWakeWord model: %s", model_name)
        self.model_name = model_name
        self.threshold = threshold

        meta = openwakeword.models[model_name]
        model_path = meta["model_path"]
        # Score-dict key is the bundled filename stem (e.g. "hey_jarvis_v0.1").
        self._score_key = Path(model_path).stem

        self.model = openwakeword.Model(wakeword_model_paths=[model_path])

    def detect(self, audio_chunk: np.ndarray) -> bool:
        scores = self.model.predict(audio_chunk)
        score = scores.get(self._score_key, 0.0)
        return score >= self.threshold

    def reset(self) -> None:
        # openwakeword 0.4.0's Model.reset() only clears prediction_buffer.
        # The AudioFeatures preprocessor keeps a rolling mel/embedding state
        # that survives across calls, so on re-entry after a wake event the
        # next chunk scores on features primed by the prior wake utterance —
        # firing instantly. Clear those preprocessor buffers in place too.
        # Re-constructing the preprocessor would reload ONNX models (slow).
        self.model.reset()
        pre = getattr(self.model, "preprocessor", None)
        if pre is None:
            return
        if hasattr(pre, "raw_data_buffer"):
            pre.raw_data_buffer.clear()
        if hasattr(pre, "melspectrogram_buffer"):
            pre.melspectrogram_buffer = np.ones((76, 32))
        if hasattr(pre, "accumulated_samples"):
            pre.accumulated_samples = 0
        if hasattr(pre, "feature_buffer"):
            pre.feature_buffer = np.zeros_like(pre.feature_buffer)


class VadBackend(Protocol):
    """Voice activity detector. Consumes audio chunks, returns speech bool."""
    def is_speech(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class RmsVadBackend:
    """Fallback VAD — amplitude threshold. Preserves current MiniClaw behavior
    when VAD_BACKEND=rms or when Silero VAD fails to load."""

    def __init__(self, threshold: int = 1000):
        self.threshold = threshold

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.int16:
            # Treat any non-int16 input as float in [-1, 1] (the convention used
            # elsewhere in voice.py); rescale to int16 magnitude before comparing
            # to threshold. Direct astype(int16) would truncate normalized
            # samples to zero and silently report all audio as silence.
            audio_chunk = (audio_chunk * 32768.0).astype(np.int16)
        level = np.abs(audio_chunk).mean()
        return level > self.threshold

    def reset(self) -> None:
        # RMS check is stateless; nothing to clear between sessions.
        pass


try:
    import silero_vad
    import torch
    _SILERO_AVAILABLE = True
except ImportError:
    silero_vad = None  # type: ignore[assignment]
    torch = None  # type: ignore[assignment]
    _SILERO_AVAILABLE = False


class SileroVadBackend:
    """Primary VAD — Silero TorchScript speech-probability model.

    Silero VAD only accepts 512-sample frames at 16kHz. PyAudio chunks are
    typically 1024 samples, so we keep a carry-over buffer that yields exact
    512-sample frames to the model. Returns True if any frame in the current
    call's accumulated audio scored above the threshold (conservative — a
    single speech-positive frame keeps `recording` armed in the endpoint loop).
    """

    FRAME_SIZE = 512

    def __init__(self, threshold: float = 0.5):
        if not _SILERO_AVAILABLE:
            raise ImportError("silero-vad not installed")
        logger.info("Loading Silero VAD model")
        self.threshold = threshold
        self.model = silero_vad.load_silero_vad()
        self._buffer = np.zeros(0, dtype=np.float32)

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32) / 32768.0

        self._buffer = np.concatenate([self._buffer, audio_chunk])

        any_speech = False
        while len(self._buffer) >= self.FRAME_SIZE:
            frame = self._buffer[: self.FRAME_SIZE]
            self._buffer = self._buffer[self.FRAME_SIZE :]
            tensor = torch.from_numpy(frame)
            score = self.model(tensor, 16000).item()
            if score >= self.threshold:
                any_speech = True
        return any_speech

    def reset(self) -> None:
        # Clear streaming carry-over and the model's internal LSTM state.
        # The endpoint loop calls reset() between sessions so a stale tail
        # doesn't carry into the next utterance.
        self._buffer = np.zeros(0, dtype=np.float32)
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()


class WhisperBackend:
    """Default speech-to-text backend using Whisper for wake and full transcription."""

    def __init__(self, wake_model: str = "tiny", transcription_model: str = "base"):
        logger.info("Loading Whisper wake model: %s", wake_model)
        self.wake_model = whisper.load_model(wake_model)

        logger.info("Loading Whisper transcription model: %s", transcription_model)
        self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_wake_audio(self, audio_float) -> str:
        """Transcribe a wake-word detection audio window."""
        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        """Transcribe a recorded WAV file."""
        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


class HybridWhisperBackend:
    """Independent Hailo/CPU selection for wake and full transcription."""

    def __init__(
        self,
        wake_model: str,
        transcription_model: str,
        use_hailo_wake: bool,
        use_hailo_transcription: bool,
    ):
        self.use_hailo_wake = use_hailo_wake
        self.use_hailo_transcription = use_hailo_transcription

        if use_hailo_wake:
            self.hailo_wake_runtime = HailoWakeRuntime(
                model_name=wake_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper wake model: %s", wake_model)
            self.wake_model = whisper.load_model(wake_model)

        if use_hailo_transcription:
            self.hailo_runtime = HailoTranscriptionRuntime(
                model_name=transcription_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper transcription model: %s", transcription_model)
            self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_wake_audio(self, audio_float) -> str:
        if self.use_hailo_wake:
            return self.hailo_wake_runtime.transcribe_wake_audio(audio_float).strip()

        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        if self.use_hailo_transcription:
            return self.hailo_runtime.transcribe_file(audio_file).strip()

        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


def hailo_runtime_available() -> bool:
    return Path("/dev/hailo0").exists()


def hailo_transcription_assets_available(transcription_model: str) -> tuple[bool, str]:
    transcription_dir = HAILO_WHISPER_ASSET_ROOT / transcription_model

    if not transcription_dir.exists():
        return False, "transcription model asset missing"
    return True, ""


def hailo_wake_assets_available(wake_model: str) -> tuple[bool, str]:
    wake_dir = HAILO_WHISPER_ASSET_ROOT / wake_model

    if not wake_dir.exists():
        return False, "wake model asset missing"
    return True, ""


def hailo_wake_self_check(wake_model: str) -> None:
    HailoWakeRuntime.self_check(
        model_name=wake_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def hailo_transcription_self_check(transcription_model: str) -> None:
    HailoTranscriptionRuntime.self_check(
        model_name=transcription_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def build_stt_backend(
    wake_model: str, transcription_model: str
) -> tuple[SttBackend, str]:
    if not hailo_runtime_available():
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — Hailo runtime unavailable",
        )

    use_hailo_wake = False
    use_hailo_transcription = False
    fallback_reasons: list[str] = []

    if wake_model in SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS:
        wake_assets_ok, wake_reason = hailo_wake_assets_available(wake_model)
        if wake_assets_ok:
            try:
                hailo_wake_self_check(wake_model)
                use_hailo_wake = True
            except Exception as exc:
                fallback_reasons.append(f"wake {exc}")
        else:
            fallback_reasons.append(wake_reason)
    else:
        fallback_reasons.append("wake model variant unsupported by Hailo")

    if transcription_model in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        transcription_assets_ok, transcription_reason = (
            hailo_transcription_assets_available(transcription_model)
        )
        if transcription_assets_ok:
            try:
                hailo_transcription_self_check(transcription_model)
                use_hailo_transcription = True
            except Exception as exc:
                fallback_reasons.append(f"transcription {exc}")
        else:
            fallback_reasons.append(transcription_reason)
    else:
        fallback_reasons.append("transcription model variant unsupported by Hailo")

    if not use_hailo_wake and not use_hailo_transcription:
        reason = fallback_reasons[0] if fallback_reasons else "Hailo unavailable"
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — {reason}",
        )

    backend = HybridWhisperBackend(
        wake_model=wake_model,
        transcription_model=transcription_model,
        use_hailo_wake=use_hailo_wake,
        use_hailo_transcription=use_hailo_transcription,
    )
    wake_backend = f"{'hailo' if use_hailo_wake else 'cpu'}:{wake_model}"
    transcription_backend = (
        f"{'hailo' if use_hailo_transcription else 'cpu'}:{transcription_model}"
    )
    return (
        backend,
        f"STT backend: Hybrid Whisper (wake={wake_backend}, transcription={transcription_backend})",
    )


def build_wake_backend(
    backend_name: str,
    model_name: str,
    threshold: float,
    wake_phrase: str,
    whisper_model: str,
) -> tuple[WakeBackend, str]:
    """Select wake backend by name with automatic fallback to Whisper."""
    if backend_name == "openwakeword":
        try:
            backend = OpenWakeWordBackend(model_name=model_name, threshold=threshold)
            return backend, f"Wake backend: openwakeword ({model_name}, threshold={threshold})"
        except Exception:
            logger.warning(
                "openWakeWord unavailable — falling back to Whisper wake",
                exc_info=True,
            )
            backend = WhisperWakeBackend(
                model_name=whisper_model, wake_phrase=wake_phrase
            )
            return backend, f"Wake backend: whisper:{whisper_model} ('{wake_phrase}') — openwakeword fallback"

    if backend_name == "whisper":
        backend = WhisperWakeBackend(model_name=whisper_model, wake_phrase=wake_phrase)
        return backend, f"Wake backend: whisper:{whisper_model} ('{wake_phrase}')"

    raise ValueError(
        f"unknown wake backend {backend_name!r}; expected 'openwakeword' or 'whisper'"
    )


class KokoroTTSBackend:
    """Default text-to-speech backend using Kokoro with streaming playback."""

    sample_rate = KOKORO_SAMPLE_RATE

    def __init__(
        self,
        voice: str = "af_heart",
        speed: float = 1.0,
        output_device: int | None = None,
        output_samplerate: int | None = None,
    ):
        logger.info("Loading Kokoro TTS pipeline (voice: %s)...", voice)
        self.voice = voice
        self.speed = speed
        self.output_device = output_device
        self.output_samplerate = output_samplerate or KOKORO_SAMPLE_RATE
        self.pipeline = KPipeline(lang_code="a")

    def speak(self, text: str) -> None:
        """Stream generated speech directly to the output device."""
        with sd.OutputStream(
            samplerate=self.output_samplerate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        ) as stream:
            for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
                stream.write(resample(audio, KOKORO_SAMPLE_RATE, self.output_samplerate))
