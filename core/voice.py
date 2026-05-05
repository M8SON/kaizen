"""
Voice Interface - Handles microphone input (Whisper STT) and speaker output (Kokoro TTS).

Designed to be swappable — if the AI HAT+ 2 accelerates Whisper, only this module changes.

Wake word detection uses whisper-tiny on a continuous sliding audio window so any
custom phrase works without training data. The larger transcription model is only
invoked after the wake phrase is detected.
"""

import os
import wave
import tempfile
import logging
import threading
from pathlib import Path

import numpy as np
import pyaudio
import sounddevice as sd

from core import profiling
from core.audio_devices import (
    output_samplerate,
    resample,
    resolve_input_device,
    resolve_output_device,
)
from core.voice_backends import KOKORO_SAMPLE_RATE, KokoroTTSBackend, WhisperBackend

logger = logging.getLogger(__name__)

_MUSIC_ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "elevator.wav"


class VoiceInterface:
    """
    Manages audio input (recording + transcription) and output (TTS).

    Audio pipeline:
      Wake:   Microphone → PyAudio → 2s sliding window → whisper-tiny → phrase check
      Input:  Microphone → PyAudio → silence detection → whisper-base → text
      Output: text → Kokoro TTS → WAV → aplay → speaker
    """

    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000

    # Wake word window: 2s of audio, re-evaluated every 1s
    WAKE_WINDOW_SECONDS = 2.0
    WAKE_STEP_SECONDS = 1.0

    def __init__(
        self,
        whisper_model: str = "base",
        wake_model: str = "tiny",
        wake_phrase: str = "computer",
        enable_tts: bool = True,
        tts_voice: str = "af_heart",
        tts_speed: float = 1.0,
        silence_threshold: int = 1000,
        silence_duration: float = 2.0,
        stt_backend=None,
        tts_backend=None,
        wake_backend=None,
    ):
        self.enable_tts = enable_tts
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.wake_phrase = wake_phrase.lower().strip()

        self._input_device_index = resolve_input_device()
        self._output_device_index = resolve_output_device()
        self._output_samplerate = output_samplerate(self._output_device_index)

        # Shared PyAudio stream passed from wake detection to listen()
        # to avoid the teardown/setup gap between the two phases.
        self._shared_audio = None
        self._shared_stream = None

        self.stt_backend = stt_backend or WhisperBackend(
            wake_model=wake_model,
            transcription_model=whisper_model,
        )
        self.wake_backend = wake_backend  # may be None for legacy callers
        self.tts_backend = (
            tts_backend
            if tts_backend is not None
            else (
                KokoroTTSBackend(
                    voice=tts_voice,
                    speed=tts_speed,
                    output_device=self._output_device_index,
                    output_samplerate=self._output_samplerate,
                )
                if enable_tts
                else None
            )
        )

        logger.info("Models loaded — wake phrase: '%s'", self.wake_phrase)

        # Elevator-music feature state
        self._music_playing = False
        self._music_thread: threading.Thread | None = None
        self._music_enabled = (
            os.environ.get("MINICLAW_ELEVATOR_MUSIC", "true").strip().lower() != "false"
        )
        if self._music_enabled and self.enable_tts:
            self._music_buffer = self._load_music_buffer()
        else:
            self._music_buffer = None

    def _load_music_buffer(self) -> "np.ndarray | None":
        """Load and prepare assets/elevator.wav for looped playback.

        Returns a float32 mono numpy array at the output device's sample
        rate, or None if the file is missing / unreadable / unsupported.
        Failures are non-fatal: the elevator-music feature silently
        disables itself for the session.
        """
        path = _MUSIC_ASSET_PATH
        try:
            with wave.open(str(path), "rb") as wf:
                n_channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                sample_width = wf.getsampwidth()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
        except (FileNotFoundError, wave.Error, OSError) as exc:
            logger.warning("Elevator music disabled — could not load %s: %s", path, exc)
            return None

        if sample_width != 2:
            logger.warning(
                "Elevator music disabled — %s must be 16-bit PCM (got %d-bit)",
                path,
                sample_width * 8,
            )
            return None

        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        elif n_channels != 1:
            logger.warning(
                "Elevator music disabled — %s has %d channels (need 1 or 2)",
                path,
                n_channels,
            )
            return None

        if sample_rate != self._output_samplerate:
            data = resample(data, sample_rate, self._output_samplerate)
        return data.astype(np.float32, copy=False)

    def start_thinking_music(self) -> None:
        """Start looping elevator music in a background daemon thread.

        No-op when TTS is disabled, the asset failed to load, or a
        music thread is already alive.
        """
        if not self.enable_tts or self._music_buffer is None:
            return
        if self._music_thread is not None and self._music_thread.is_alive():
            return
        self._music_playing = True
        self._music_thread = threading.Thread(
            target=self._music_loop, daemon=True, name="elevator-music"
        )
        self._music_thread.start()

    def _music_loop(self) -> None:
        # One persistent OutputStream for the whole music session — the
        # earlier sd.play/sd.wait pattern reused PortAudio's default
        # stream, and the rapid open/close churn against ALSA + USB audio
        # triggered snd_async_del_handler asserts that aborted the process
        # after a few turns.
        chunk = 1024
        buf = self._music_buffer
        pos = 0
        try:
            with sd.OutputStream(
                samplerate=self._output_samplerate,
                device=self._output_device_index,
                channels=1,
                dtype="float32",
            ) as stream:
                while self._music_playing:
                    end = pos + chunk
                    if end <= len(buf):
                        stream.write(buf[pos:end])
                        pos = end
                    else:
                        head = buf[pos:]
                        tail = buf[: chunk - len(head)]
                        stream.write(np.concatenate([head, tail]))
                        pos = len(tail)
        except Exception as exc:
            logger.warning("Elevator music error: %s", exc, exc_info=True)
            self._music_playing = False

    def stop_thinking_music(self) -> None:
        """Hard-stop the music loop. Idempotent."""
        if not self._music_playing:
            return
        self._music_playing = False
        if self._music_thread is not None:
            self._music_thread.join(timeout=1.0)
            self._music_thread = None

    def wait_for_wake_word(self) -> bool:
        """
        Block until the wake phrase is detected in the microphone stream.

        Continuously records audio in a sliding 2-second window and runs
        whisper-tiny on each window. Returns True when the wake phrase is heard,
        False if interrupted by Ctrl+C.

        On detection the PyAudio stream is kept open and stored in self._shared_stream
        so that listen() can start capturing immediately with no gap.
        """
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            input_device_index=self._input_device_index,
            frames_per_buffer=self.CHUNK,
        )

        window_samples = int(self.RATE * self.WAKE_WINDOW_SECONDS)
        step_samples = int(self.RATE * self.WAKE_STEP_SECONDS)
        samples_collected = 0
        buffer = []

        logger.info("Waiting for wake phrase: '%s'", self.wake_phrase)

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                buffer.append(np.frombuffer(data, dtype=np.int16))
                samples_collected += self.CHUNK

                if samples_collected < step_samples:
                    continue

                samples_collected = 0

                # Build window from buffer, trim to last 2 seconds
                window = np.concatenate(buffer)
                if len(window) > window_samples:
                    window = window[-window_samples:]
                    buffer = [window]

                # Transcribe window with tiny model
                audio_float = window.astype(np.float32) / 32768.0

                if self.wake_backend is not None:
                    detected = self.wake_backend.detect(audio_float)
                else:
                    transcript = self.stt_backend.transcribe_wake_audio(audio_float)
                    if transcript:
                        logger.info("Wake window heard: '%s'", transcript)
                    detected = self.wake_phrase in transcript

                if detected:
                    logger.info("Wake detected")
                    # Keep stream open — listen() will use it immediately
                    self._shared_audio = audio
                    self._shared_stream = stream
                    return True

        except KeyboardInterrupt:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            return False
        except Exception:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            raise

    def listen(self, max_wait_seconds: float = 0, on_speech_done=None) -> str | None:
        """
        Record audio until silence is detected, then transcribe with the full model.

        Reuses the stream left open by wait_for_wake_word() if available, so
        recording starts instantly with no setup gap.

        max_wait_seconds: give up and return None if no speech starts within this many
        seconds (0 = wait forever). Used for conversation idle timeout.

        on_speech_done: optional zero-arg callable fired the moment speech-then-
        silence is detected, before transcription. Used to start audio feedback
        (e.g. elevator music) over the STT wait so the user doesn't hear silence.
        Not called when max_wait_seconds elapses without any speech.
        """
        audio_file = self._record_until_silence(
            max_wait_seconds=max_wait_seconds,
            on_speech_done=on_speech_done,
        )
        try:
            transcription = self._transcribe(audio_file)
        finally:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

        if not transcription or len(transcription.strip()) < 3:
            return None

        return transcription.strip()

    def _r2_chirp(self, freq_start, freq_end, duration, volume=0.45, vibrato_hz=0, vibrato_depth=0):
        """Frequency-sweep chirp with optional vibrato — the core R2-D2 building block.

        vibrato_hz: LFO rate in Hz (0 = off). Modulates instantaneous frequency to
        produce the characteristic wobbly droid quality.
        vibrato_depth: frequency deviation in Hz at peak LFO swing.
        """
        n = int(KOKORO_SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, False)
        freq = np.linspace(freq_start, freq_end, n)
        if vibrato_hz > 0:
            freq = freq + vibrato_depth * np.sin(2 * np.pi * vibrato_hz * t)
        phase = np.cumsum(2 * np.pi * freq / KOKORO_SAMPLE_RATE)
        env = np.ones(n)
        a, d = max(1, int(n * 0.08)), max(1, int(n * 0.25))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(phase) * env * volume).astype(np.float32)

    def _r2_beep(self, freq, duration, volume=0.4):
        """Short pure-tone beep — punctuation between R2-D2 chirps."""
        n = int(KOKORO_SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, False)
        env = np.ones(n)
        a, d = max(1, int(n * 0.05)), max(1, int(n * 0.35))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(2 * np.pi * freq * t) * env * volume).astype(np.float32)

    def play_startup_sound(self):
        """Play an R2-D2-style happy greeting sequence on startup."""
        if not self.enable_tts:
            return
        try:
            g  = np.zeros(int(KOKORO_SAMPLE_RATE * 0.04), dtype=np.float32)
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
            sound = np.concatenate([
                # Opening ascending wobble sweep
                self._r2_chirp(480, 1600, 0.17, vibrato_hz=10, vibrato_depth=90),
                g,
                # Staccato arpeggio burst
                self._r2_beep(1800, 0.06), gs,
                self._r2_beep(1400, 0.05), gs,
                self._r2_beep(2000, 0.05), gs,
                self._r2_beep(1600, 0.05),
                g,
                # Descending wobble — question/acknowledgement feel
                self._r2_chirp(1700, 750, 0.15, vibrato_hz=13, vibrato_depth=110),
                g,
                # Rising two-note finish — happy affirmation
                self._r2_beep(1500, 0.06), gs,
                self._r2_beep(2200, 0.10, volume=0.5),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            sd.wait()
        except Exception as e:
            logger.warning("Startup sound error: %s", e)

    def play_thinking_sound(self):
        """Play a short R2-D2-style curious warble while processing a request."""
        if not self.enable_tts:
            return
        try:
            g  = np.zeros(int(KOKORO_SAMPLE_RATE * 0.03), dtype=np.float32)
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
            sound = np.concatenate([
                # Quick ascending wobble — "hmm, let me think"
                self._r2_chirp(780, 1700, 0.11, vibrato_hz=9, vibrato_depth=80),
                g,
                # Staccato pair
                self._r2_beep(1900, 0.06), gs,
                self._r2_beep(1500, 0.05),
                g,
                # Descending wobble close
                self._r2_chirp(1600, 900, 0.11, vibrato_hz=11, vibrato_depth=90),
                g,
                self._r2_beep(1650, 0.07),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            sd.wait()
        except Exception as e:
            logger.warning("Thinking sound error: %s", e)

    def speak(self, text: str):
        """Speak text aloud using Kokoro TTS with streaming playback.

        Each Kokoro chunk is written to a sounddevice OutputStream as it is
        generated, so the first words play immediately without waiting for the
        full response to be synthesised.
        """
        if not self.enable_tts or self.tts_backend is None:
            return

        try:
            self.tts_backend.speak(text)
        except Exception as e:
            logger.warning("TTS error: %s", e)

    def _record_until_silence(self, max_wait_seconds: float = 0, on_speech_done=None) -> str:
        """Record audio with automatic silence detection, return temp WAV file path.

        Reuses self._shared_stream if set by wait_for_wake_word(), then clears it.
        max_wait_seconds: stop early if no speech starts within this window (0 = wait forever).
        on_speech_done: fired once when speech-then-silence is detected, before the
        WAV is finalized. Not fired when max_wait elapses with no speech.
        """
        # Reuse the open stream from wake detection if available
        if self._shared_stream is not None:
            audio = self._shared_audio
            stream = self._shared_stream
            self._shared_audio = None
            self._shared_stream = None
        else:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self._input_device_index,
                frames_per_buffer=self.CHUNK,
            )

        logger.info("Recording...")

        frames = []
        silence_frames = 0
        silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
        max_wait_chunks = int(self.RATE / self.CHUNK * max_wait_seconds) if max_wait_seconds else 0
        waited_chunks = 0
        recording = False

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                frames.append(data)

                level = np.abs(np.frombuffer(data, dtype=np.int16)).mean()

                if level > self.silence_threshold:
                    recording = True
                    silence_frames = 0
                elif recording:
                    silence_frames += 1

                if recording and silence_frames > silence_limit:
                    if on_speech_done is not None:
                        try:
                            on_speech_done()
                        except Exception:
                            logger.warning("on_speech_done callback raised", exc_info=True)
                    break

                # Idle timeout: give up if no speech started within max_wait_seconds
                if not recording:
                    waited_chunks += 1
                    if max_wait_chunks and waited_chunks > max_wait_chunks:
                        break

        except KeyboardInterrupt:
            pass
        finally:
            sample_width = audio.get_sample_size(self.FORMAT)
            stream.stop_stream()
            stream.close()
            audio.terminate()

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(temp_file.name, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(sample_width)
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(frames))

        return temp_file.name

    def _transcribe(self, audio_file: str) -> str:
        """Transcribe a WAV file using the full Whisper model."""
        logger.info("Transcribing...")
        with profiling.stage("stt"):
            text = self.stt_backend.transcribe_file(audio_file)
        logger.info("Transcribed: %s", text)
        return text
