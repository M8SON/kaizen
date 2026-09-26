"""
Voice Interface - Handles microphone input (Whisper STT) and speaker output (Kokoro TTS).

Designed to be swappable — if the AI HAT+ 2 accelerates Whisper, only this module changes.

Wake detection runs through openWakeWord (purpose-built keyword spotter); the
Whisper transcription model is only invoked after the wake word fires.
"""

import os
import re
import wave
import collections
import tempfile
import logging
import threading

import numpy as np
import pyaudio
import sounddevice as sd

from core import profiling
from core.audio_devices import (
    output_samplerate,
    resample,
    resolve_input_device,
    resolve_output_device,
    input_channel_count,
)
from core.voice_backends import KOKORO_SAMPLE_RATE, KokoroTTSBackend, WhisperBackend

logger = logging.getLogger(__name__)


# Output buffer for cached filler/answer phrases. They start while the main
# thread runs CPU-heavy work (skill-selector ONNX embedding, docker tool
# launch); with the default ~35ms buffer that starved the audio callback and
# underran at ~240-270ms — right on the first syllable ("...et me check").
# Measured on the Pi 5: 300ms buffer -> zero underruns under the same load.
PHRASE_OUTPUT_LATENCY_S = 0.3


def _quiet_points(seg: "np.ndarray", min_run: int) -> "np.ndarray":
    """Sorted sample offsets where `seg` is at rest: 0, the start of every
    silent run >= min_run samples, and len(seg). The pre-buffer cue stops at
    the next of these so it ends between bloops rather than mid-sound."""
    silent = np.concatenate(([False], np.abs(seg) < 1e-4, [False]))
    edges = np.flatnonzero(np.diff(silent.astype(np.int8)))
    starts, ends = edges[0::2], edges[1::2]
    points = starts[(ends - starts) >= min_run]
    return np.unique(np.concatenate(([0], points, [len(seg)])))


class VoiceInterface:
    """
    Manages audio input (recording + transcription) and output (TTS).

    Audio pipeline:
      Wake:   Microphone → PyAudio → openWakeWord → trigger
      Input:  Microphone → PyAudio → VAD → whisper-base → text
      Output: text → Kokoro TTS → WAV → aplay → speaker
    """

    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000

    def __init__(
        self,
        transcription_model: str = "base",
        enable_tts: bool = True,
        tts_voice: str = "af_heart",
        tts_speed: float = 1.0,
        silence_threshold: int = 1000,
        silence_duration: float = 2.0,
        stt_backend=None,
        tts_backend=None,
        wake_backend=None,
        display_wake_word: str = "hey jarvis",
        vad_backend=None,
        vad_min_silence_ms: int = 700,
        barge_in_enabled: bool = False,
        streaming_stt=None,
        wake_backend_alt=None,
    ):
        self.enable_tts = enable_tts
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.display_wake_word = display_wake_word.strip()

        self._input_device_index = resolve_input_device()
        # Multi-channel mics are captured in stereo and split. On the XVF3800
        # channel 0 is heavily suppressed while audio plays (music: 0/7 wake
        # words, speech mostly lost) whereas channel 1 kept every word (7/7
        # transcribed) — measured on the Pi 2026-09-26 — so channel 1 is the
        # primary (recording/STT/VAD). The wake word runs on both channels
        # (either fires): in a quiet room each channel missed a *different*
        # 5-ft attempt, together 6/6.
        self._capture_channels = input_channel_count(self._input_device_index)
        self._primary_channel = (
            min(int(os.getenv("MIC_CHANNEL", "1")), self._capture_channels - 1)
            if self._capture_channels > 1 else 0
        )
        logger.info("Mic capture: %d channel(s), primary channel %d",
                    self._capture_channels, self._primary_channel)
        self._output_device_index = resolve_output_device()
        self._output_samplerate = output_samplerate(self._output_device_index)

        # Shared PyAudio stream passed from wake detection to listen()
        # to avoid the teardown/setup gap between the two phases.
        self._shared_audio = None
        self._shared_stream = None
        # Audio the wake detector consumed just before firing. openWakeWord
        # fires a beat after "jarvis" ends, so words spoken straight after the
        # wake word land here; listen() prepends it so they reach Whisper.
        self._wake_preroll_ms = int(os.getenv("WAKE_PREROLL_MS", "600"))
        self._wake_preroll = b""
        self._used_preroll = False
        # Optional streaming STT (core.meta_stt.MetaStreamingStt): audio goes
        # out while the user talks; local Whisper remains the fallback.
        self._streaming_stt = streaming_stt
        self._stream_session = None
        self._heard_speech = False

        # Active PyAudio resources tracked here so shutdown() (e.g. from a
        # SIGINT handler) can close them even if the wake/listen loop is
        # blocked in PortAudio's C-extension when the signal arrives.
        self._active_audio = None
        self._active_stream = None

        # Barge-in: wake-word watcher active only during response playback.
        # Handle is (audio, stream, stop_event, thread) or None.
        self.barge_in_enabled = barge_in_enabled
        self._barge_in = None

        # Looping pre-buffer cue: (stop_event, thread) or None. The thread
        # owns its own OutputStream's entire lifecycle (open, write, close).
        self._prebuffer_cue = None
        self._prebuffer_cue_lock = threading.Lock()

        self.stt_backend = stt_backend or WhisperBackend(
            transcription_model=transcription_model,
        )
        if wake_backend is None:
            raise ValueError(
                "VoiceInterface requires a wake_backend; build_wake_backend "
                "constructs the default openWakeWord backend"
            )
        self.wake_backend = wake_backend
        # Second detector for the non-primary channel (stateful, so one per
        # channel). Unused for mono mics.
        self.wake_backend_alt = wake_backend_alt if self._capture_channels > 1 else None
        self.vad_backend = vad_backend
        self.vad_min_silence_ms = vad_min_silence_ms
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

        logger.info("Models loaded — wake word: '%s'", self.display_wake_word)

    @staticmethod
    def _close_pyaudio(audio, stream) -> None:
        """Best-effort PyAudio teardown that swallows late-stage errors.

        Used by both the normal wake/listen exit paths and shutdown()."""
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass

    def _open_mic(self, audio):
        return audio.open(
            format=self.FORMAT,
            channels=self._capture_channels,
            rate=self.RATE,
            input=True,
            input_device_index=self._input_device_index,
            frames_per_buffer=self.CHUNK,
        )

    def _split_channels(self, data: bytes) -> list:
        """Interleaved int16 capture -> one contiguous array per channel."""
        samples = np.frombuffer(data, dtype=np.int16)
        if self._capture_channels == 1:
            return [samples]
        frames = samples.reshape(-1, self._capture_channels)
        return [np.ascontiguousarray(frames[:, c]) for c in range(self._capture_channels)]

    def _wake_detected(self, channels: list) -> bool:
        """Feed every channel's detector every chunk (openWakeWord's buffers
        must stay primed) and fire if either one crosses its threshold."""
        hit = self.wake_backend.detect(channels[self._primary_channel])
        if self.wake_backend_alt is not None:
            other = channels[1 - self._primary_channel]
            hit = self.wake_backend_alt.detect(other) or hit
        return hit

    def _reset_wake(self) -> None:
        self.wake_backend.reset()
        if self.wake_backend_alt is not None:
            self.wake_backend_alt.reset()

    def _start_barge_in_watcher(self, interrupt_event) -> None:
        """Run a wake-word watcher on its own mic stream during playback.

        Reuses self.wake_backend (the main wake loop is idle mid-conversation).
        On a wake hit it sets interrupt_event, which the TTS writer polls to
        cut playback. No-op when disabled; if the mic can't be opened (device
        busy / no full-duplex on the XVF3800) the feature goes silently
        inactive this turn rather than crashing the loop."""
        if not self.barge_in_enabled:
            return
        try:
            audio = pyaudio.PyAudio()
            stream = self._open_mic(audio)
        except Exception as e:
            logger.warning(
                "Barge-in watcher could not open mic; inactive this turn: %s", e
            )
            self._barge_in = None
            return

        # Clear stale features so the watcher doesn't fire on the tail of the
        # prior wake event (same reasoning as wait_for_wake_word).
        self._reset_wake()
        stop_event = threading.Event()

        def _watch():
            try:
                while not stop_event.is_set():
                    data = stream.read(self.CHUNK, exception_on_overflow=False)
                    if self._wake_detected(self._split_channels(data)):
                        logger.info("Barge-in wake word detected")
                        interrupt_event.set()
                        return
            except Exception:
                logger.exception("Barge-in watcher thread error")

        thread = threading.Thread(target=_watch, daemon=True, name="barge-in-watcher")
        thread.start()
        self._barge_in = (audio, stream, stop_event, thread)
        logger.info("Barge-in watcher active (say the wake word to interrupt)")

    def _stop_barge_in_watcher(self) -> None:
        """Stop and tear down the watcher. Idempotent."""
        handle = self._barge_in
        if handle is None:
            return
        audio, stream, stop_event, thread = handle
        stop_event.set()
        thread.join(timeout=2.0)
        if thread.is_alive():
            logger.warning("Barge-in watcher did not join within 2s")
        self._close_pyaudio(audio, stream)
        self._barge_in = None

    def shutdown(self) -> None:
        """Release every audio resource this VoiceInterface owns.

        Idempotent and exception-safe so it can run from a signal handler
        or a finally block. Closes the shared wake→listen handoff stream
        and any stream currently being read by wait_for_wake_word /
        _record_until_silence (PyAudio's C-level stream.read can pin
        /dev/snd until the device is explicitly terminated, which strands
        the next ./run.sh --voice with Errno -9996 on the XVF3800)."""
        self._stop_barge_in_watcher()
        self._close_pyaudio(self._shared_audio, self._shared_stream)
        self._shared_audio = None
        self._shared_stream = None
        self._close_pyaudio(self._active_audio, self._active_stream)
        self._active_audio = None
        self._active_stream = None

    def wait_for_wake_word(self) -> bool:
        """
        Block until the wake word is detected in the microphone stream.

        Streams audio chunks straight into the openWakeWord detector, which
        maintains its own rolling feature buffer. Returns True when the
        wake word fires, False if interrupted by Ctrl+C.

        On detection the PyAudio stream is kept open and stored in
        self._shared_stream so that listen() can start capturing
        immediately with no gap.
        """
        audio = pyaudio.PyAudio()
        stream = self._open_mic(audio)
        self._active_audio = audio
        self._active_stream = stream

        # openWakeWord accumulates a rolling feature buffer across calls.
        # Without a reset between sessions, the next wake-loop entry sees
        # the tail of the prior wake event still in the model's buffer
        # and fires immediately.
        self._reset_wake()

        logger.info("Waiting for wake word: '%s'", self.display_wake_word)

        chunk_ms = self.CHUNK / self.RATE * 1000
        preroll = collections.deque(maxlen=max(0, int(-(-self._wake_preroll_ms // chunk_ms))))

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                channels = self._split_channels(data)
                preroll.append(channels[self._primary_channel].tobytes())

                if self._wake_detected(channels):
                    logger.info("Wake detected")
                    self._wake_preroll = b"".join(preroll)
                    self._shared_audio = audio
                    self._shared_stream = stream
                    self._active_audio = None
                    self._active_stream = None
                    return True

        except KeyboardInterrupt:
            self._close_pyaudio(audio, stream)
            self._active_audio = None
            self._active_stream = None
            return False
        except Exception:
            self._close_pyaudio(audio, stream)
            self._active_audio = None
            self._active_stream = None
            raise

    def listen(self, max_wait_seconds: float = 0, on_speech_done=None) -> str | None:
        """
        Record audio until silence is detected, then transcribe with the full model.

        Reuses the stream left open by wait_for_wake_word() if available, so
        recording starts instantly with no setup gap.

        max_wait_seconds: give up and return None if no speech starts within this many
        seconds (0 = wait forever). Used for conversation idle timeout.

        on_speech_done: optional zero-arg callable fired the moment speech-then-
        silence is detected, before transcription. Used to overlap an audio
        cue with the STT wait. Not called when max_wait_seconds elapses
        without any speech.
        """
        audio_file = self._record_until_silence(
            max_wait_seconds=max_wait_seconds,
            on_speech_done=on_speech_done,
        )
        session, self._stream_session = self._stream_session, None
        if not self._heard_speech:
            # VAD never detected speech (idle timeout): nothing to transcribe,
            # so don't spend ~1.8s of Whisper CPU on silence.
            try:
                os.unlink(audio_file)
            except OSError:
                pass
            return None
        try:
            transcription = None
            if session is not None:
                with profiling.stage("stt"):
                    transcription = session.finish()
                if transcription is not None:
                    logger.info("Transcribed (streaming): %s", transcription)
            if transcription is None:
                transcription = self._transcribe(audio_file)
        finally:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

        if transcription and self._used_preroll:
            transcription = self._strip_wake_phrase(transcription)

        if not transcription or len(transcription.strip()) < 3:
            return None

        return transcription.strip()

    def _strip_wake_phrase(self, text: str) -> str:
        """Drop a leading "hey jarvis" that the wake pre-roll let Whisper hear."""
        name = self.display_wake_word.split()[-1] if self.display_wake_word else ""
        if not name:
            return text
        return re.sub(rf"^\W*(?:hey\W+)?{re.escape(name)}\b\W*", "", text, flags=re.IGNORECASE)

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
        a, d = max(1, int(n * 0.08)), max(1, int(n * 0.35))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(phase) * env * volume).astype(np.float32)

    def _r2_beep(self, freq, duration, volume=0.4):
        """Short pure-tone beep — punctuation between R2-D2 chirps."""
        n = int(KOKORO_SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, False)
        env = np.ones(n)
        a, d = max(1, int(n * 0.05)), max(1, int(n * 0.50))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(2 * np.pi * freq * t) * env * volume).astype(np.float32)

    def _r2_tail(self, duration: float = 0.10) -> "np.ndarray":
        """Trailing zero buffer — gives PortAudio time to drain before stream
        close so the last beep doesn't get clipped by the device teardown."""
        return np.zeros(int(KOKORO_SAMPLE_RATE * duration), dtype=np.float32)

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
                self._r2_tail(),
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
        """Short R2-D2-style curious warble — fires from on_speech_done the
        instant the user stops talking, signalling "heard you, processing".
        Plays non-blocking (no sd.wait) so it overlaps the STT + routing +
        LLM time-to-first-token wait instead of delaying it."""
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
                self._r2_tail(),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            # Intentionally no sd.wait — caller continues straight into STT.
        except Exception as e:
            logger.warning("Thinking sound error: %s", e)

    def play_response_ready_sound(self):
        """Short R2-D2-style 'response ready' cue — fires the moment the LLM
        starts streaming text, before Kokoro's first audio. Plays
        non-blocking (no sd.wait) so Kokoro synthesis runs in parallel and
        no extra latency is added; PipeWire mixes if any overlap occurs.
        """
        if not self.enable_tts:
            return
        try:
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
            sound = np.concatenate([
                self._r2_chirp(900, 1900, 0.10, vibrato_hz=12, vibrato_depth=70),
                gs,
                self._r2_beep(2100, 0.06, volume=0.45),
                self._r2_tail(0.05),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            # Intentionally no sd.wait — caller continues to Kokoro synth.
        except Exception as e:
            logger.warning("Response-ready sound error: %s", e)

    def _prebuffer_cue_segment(self) -> "np.ndarray":
        """One R2-D2 'questioning bloops' segment (~0.44s), looped by the cue."""
        gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.03), dtype=np.float32)
        gs2 = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
        sound = np.concatenate([
            self._r2_beep(800, 0.06),
            gs,
            self._r2_beep(1200, 0.06),
            gs,
            self._r2_chirp(1000, 2300, 0.18, vibrato_hz=10, vibrato_depth=60),
            gs2,
            self._r2_beep(1700, 0.05),
        ])
        return resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate)

    def start_prebuffer_cue(self) -> None:
        """Start looping the R2-D2 cue on its own output stream until
        stop_prebuffer_cue() is called. Idempotent; errors swallowed.

        The loop thread opens and closes its own OutputStream so the stream's
        entire lifecycle lives on one thread — stop_prebuffer_cue() only
        signals and joins, it never touches the stream itself. This avoids a
        close()/write() race when stop is triggered from a different thread
        (e.g. Kokoro's writer thread via on_first_audio) than the one that
        started the cue."""
        if not self.enable_tts:
            return
        with self._prebuffer_cue_lock:
            if self._prebuffer_cue is not None:
                return
            try:
                seg = self._prebuffer_cue_segment()
                quiet = _quiet_points(seg, int(0.01 * self._output_samplerate))
                stop_event = threading.Event()

                def _loop():
                    SUB = 1024
                    pos = 0
                    try:
                        with sd.OutputStream(
                            samplerate=self._output_samplerate,
                            channels=1,
                            dtype="float32",
                            device=self._output_device_index,
                        ) as stream:
                            while True:
                                if stop_event.is_set():
                                    # Let the in-flight bloop finish to the next
                                    # silent gap (<= one element, ~0.18s) instead
                                    # of chopping it mid-sound; PipeWire mixes the
                                    # tail with the start of speech.
                                    end = quiet[np.searchsorted(quiet, pos)]
                                    if end > pos:
                                        stream.write(seg[pos:end])
                                    return
                                block = seg[pos : pos + SUB]
                                stream.write(block)
                                pos += len(block)
                                if pos >= len(seg):
                                    pos = 0
                    except Exception:
                        logger.exception("Pre-buffer cue loop error")

                thread = threading.Thread(target=_loop, daemon=True, name="prebuffer-cue")
                thread.start()
                self._prebuffer_cue = (stop_event, thread)
            except Exception as e:
                logger.warning("Pre-buffer cue start error: %s", e)
                self._prebuffer_cue = None

    def stop_prebuffer_cue(self) -> None:
        """Stop the looping cue. Idempotent. Only signals — never joins: the
        loop thread finishes its current bloop and closes its own stream, and
        this runs on the TTS writer thread (via on_first_audio), which must
        not stall while that tail plays."""
        with self._prebuffer_cue_lock:
            handle = self._prebuffer_cue
            self._prebuffer_cue = None
        if handle is None:
            return
        stop_event, _thread = handle
        stop_event.set()

    def play_ack_sound(self):
        """Short R2-D2-style acknowledgement chime — replaces verbal
        confirmations for music-control transport commands ("Paused.",
        "Resumed.", etc.). Plays non-blocking so the user gets near-instant
        audio feedback. Errors are logged and swallowed so a missing
        speaker can't crash the voice loop."""
        if not self.enable_tts:
            return
        try:
            sound = np.concatenate([
                # Quick rising chirp ~80ms with gentle vibrato — "got it".
                self._r2_chirp(1100, 1700, 0.08, vibrato_hz=10, vibrato_depth=40),
                self._r2_tail(0.04),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            # Intentionally no sd.wait — non-blocking ack.
        except Exception as e:
            logger.warning("Ack sound error: %s", e)

    def play_filler(self, category: str) -> None:
        """Play a pre-cached filler phrase for `category`.

        Picks a random cached phrase from ~/.kaizen/filler_audio/<category>/
        (built by scripts/build_filler_audio.py) and plays it non-blocking,
        same pattern as play_ack_sound. No-op (logged) if the category has
        no cached audio — never raises, never blocks the caller.
        """
        if not self.enable_tts:
            return
        try:
            import random
            from pathlib import Path

            cat_dir = Path.home() / ".kaizen" / "filler_audio" / category
            candidates = sorted(cat_dir.glob("*.npy")) if cat_dir.is_dir() else []
            if not candidates:
                logger.warning(
                    "play_filler: no cached audio for category %r (run "
                    "scripts/build_filler_audio.py) — skipping", category,
                )
                return

            audio = np.load(random.choice(candidates))
            sd.play(
                resample(audio, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
                latency=PHRASE_OUTPUT_LATENCY_S,
            )
            # Intentionally no sd.wait — caller continues into process_message.
        except Exception as e:
            logger.warning("Filler playback error: %s", e)

    def play_answer(self, category: str, text: str) -> bool:
        """Play the cached audio for a full canned answer, blocking.

        Unlike play_filler this waits for playback to finish — the answer is
        the whole reply, and the next listen() must not hear it. Returns
        False (caller falls back to Claude) when TTS is off, the phrase has
        no cached audio, or playback fails. Never raises.
        """
        if not self.enable_tts:
            return False
        try:
            from core.filler_classifier import FILLER_AUDIO_ROOT, phrase_slug

            path = FILLER_AUDIO_ROOT / category / f"{phrase_slug(text)}.npy"
            if not path.exists():
                logger.warning(
                    "play_answer: no cached audio for %r (run "
                    "scripts/build_filler_audio.py) — deferring to Claude", text,
                )
                return False

            audio = np.load(path)
            sd.play(
                resample(audio, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
                latency=PHRASE_OUTPUT_LATENCY_S,
            )
            sd.wait()
            return True
        except Exception as e:
            logger.warning("Answer playback error: %s", e)
            return False

    def speak(self, text: str, interruptible: bool = False) -> bool:
        """Speak text aloud using Kokoro TTS with streaming playback.

        Each Kokoro chunk is written to a sounddevice OutputStream as it is
        generated, so the first words play immediately without waiting for the
        full response to be synthesised.

        When interruptible and barge-in is enabled, a wake-word watcher runs
        during playback; saying the wake word cuts playback. Returns whether a
        barge-in fired (always False when not interruptible / TTS disabled).
        """
        if not self.enable_tts or self.tts_backend is None:
            return False

        interrupt_event = (
            threading.Event() if interruptible and self.barge_in_enabled else None
        )
        if interrupt_event is not None:
            self._start_barge_in_watcher(interrupt_event)
        try:
            self.tts_backend.speak(text, interrupt_event=interrupt_event)
        except Exception as e:
            logger.warning("TTS error: %s", e)
        finally:
            if interrupt_event is not None:
                self._stop_barge_in_watcher()
        return interrupt_event is not None and interrupt_event.is_set()

    def speak_stream_feeder(self, on_first_chunk=None, on_first_audio=None, interruptible=False):
        """Return (push, finalize) for feeding text deltas into a streaming TTS run.

        The Kokoro consumer thread is spawned LAZILY on the first non-empty
        delta — we don't claim the audio device until we have something to
        speak.

        on_first_chunk: optional zero-arg callable fired exactly once when the
        first non-empty delta arrives. The voice loop uses this to play a
        short R2-D2 'response ready' cue right before Kokoro starts.

        on_first_audio: optional zero-arg callable forwarded to the backend's
        speak_stream, fired when audio actually starts playing.

        interruptible: when True and barge-in is enabled, a wake-word watcher
        runs during playback and finalize() returns whether it fired.

        When TTS is disabled or no backend is configured, push is a no-op
        and finalize returns False immediately — callers get a uniform
        interface regardless of TTS availability.
        """
        import queue

        if not self.enable_tts or self.tts_backend is None or not hasattr(
            self.tts_backend, "speak_stream"
        ):
            def _push(_delta: str) -> None:
                return
            def _finalize() -> bool:
                return False
            return _push, _finalize

        interrupt_event = (
            threading.Event() if interruptible and self.barge_in_enabled else None
        )
        q: queue.Queue = queue.Queue()
        SENTINEL = object()
        backend = self.tts_backend
        thread_holder: list = [None]
        first_chunk_seen = [False]

        def _gen():
            while True:
                item = q.get()
                if item is SENTINEL:
                    return
                yield item

        def _consume():
            try:
                backend.speak_stream(
                    _gen(), interrupt_event=interrupt_event, on_first_audio=on_first_audio
                )
            except Exception:
                logger.exception("speak_stream consumer raised")

        def _ensure_thread() -> None:
            if thread_holder[0] is not None:
                return
            t = threading.Thread(target=_consume, daemon=True, name="kokoro-stream")
            t.start()
            thread_holder[0] = t

        def push(delta: str) -> None:
            if not delta:
                return
            if not first_chunk_seen[0]:
                first_chunk_seen[0] = True
                if on_first_chunk is not None:
                    try:
                        on_first_chunk()
                    except Exception:
                        logger.exception("on_first_chunk hook raised")
                if interrupt_event is not None:
                    self._start_barge_in_watcher(interrupt_event)
                _ensure_thread()
            q.put(delta)

        def finalize() -> bool:
            if thread_holder[0] is None:
                # No deltas ever arrived; nothing to drain, join, or stop.
                return False
            q.put(SENTINEL)
            # Pi 5 Kokoro can spend 30-60s synthesising a multi-sentence
            # response. 300s is large enough for any reasonable response;
            # anything longer is a real deadlock and recovery must be manual.
            thread_holder[0].join(timeout=300)
            if thread_holder[0].is_alive():
                logger.warning(
                    "Kokoro stream thread did not finish within 300s — "
                    "audio device may be stuck; subsequent turns may glitch"
                )
            if interrupt_event is not None:
                self._stop_barge_in_watcher()
            return interrupt_event is not None and interrupt_event.is_set()

        return push, finalize

    def _record_until_silence(self, max_wait_seconds: float = 0, on_speech_done=None) -> str:
        """Record audio with automatic silence detection, return temp WAV file path.

        Reuses self._shared_stream if set by wait_for_wake_word(), then clears it.
        max_wait_seconds: stop early if no speech starts within this window (0 = wait forever).
        on_speech_done: fired once when speech-then-silence is detected, before the
        WAV is finalized. Not fired when max_wait elapses with no speech.
        """
        # Reuse the open stream from wake detection if available
        preroll = b""
        if self._shared_stream is not None:
            audio = self._shared_audio
            stream = self._shared_stream
            self._shared_audio = None
            self._shared_stream = None
            preroll, self._wake_preroll = self._wake_preroll, b""
        else:
            audio = pyaudio.PyAudio()
            stream = self._open_mic(audio)
        self._active_audio = audio
        self._active_stream = stream

        logger.info("Recording...")

        if self.vad_backend is not None:
            self.vad_backend.reset()

        # Pre-roll is prepended but not fed to the VAD, so it never counts as
        # the start of speech or affects the idle timeout.
        frames = [preroll] if preroll else []
        self._used_preroll = bool(preroll)
        session = self._streaming_stt.start() if self._streaming_stt is not None else None
        if session is not None and preroll:
            session.push(preroll)
        silence_frames = 0
        silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
        max_wait_chunks = int(self.RATE / self.CHUNK * max_wait_seconds) if max_wait_seconds else 0
        waited_chunks = 0
        recording = False
        chunk_ms = int(self.CHUNK / self.RATE * 1000)
        silence_ms = 0

        ended_normally = False
        try:
            while True:
                chunk_int16 = self._split_channels(
                    stream.read(self.CHUNK, exception_on_overflow=False)
                )[self._primary_channel]
                data = chunk_int16.tobytes()  # mono: WAV, streaming STT, VAD
                frames.append(data)
                if session is not None:
                    session.push(data)

                if self.vad_backend is not None:
                    is_speech = self.vad_backend.is_speech(chunk_int16)
                else:
                    level = np.abs(chunk_int16).mean()
                    is_speech = level > self.silence_threshold

                if is_speech:
                    recording = True
                    silence_ms = 0
                    silence_frames = 0
                elif recording:
                    silence_ms += chunk_ms
                    silence_frames += 1

                if self.vad_backend is not None:
                    endpoint = recording and silence_ms >= self.vad_min_silence_ms
                else:
                    endpoint = recording and silence_frames > silence_limit

                if endpoint:
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
            ended_normally = True

        except KeyboardInterrupt:
            # Re-raise so main's outer 'except KeyboardInterrupt' runs the
            # shutdown path. Swallowing it here previously made Ctrl+C silently
            # end the current session and bounce back into the wake loop —
            # the program kept running and Mason had to kill the terminal.
            raise
        finally:
            # Hand the session to listen() only when speech was captured;
            # otherwise (idle timeout, Ctrl+C) drop it.
            self._heard_speech = recording
            if session is not None:
                if recording and ended_normally:
                    self._stream_session = session
                else:
                    session.abort()
            sample_width = audio.get_sample_size(self.FORMAT)
            self._close_pyaudio(audio, stream)
            self._active_audio = None
            self._active_stream = None

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
