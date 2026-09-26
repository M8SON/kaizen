"""Tests for the wake-word pre-roll: audio the wake detector consumed just
before firing is prepended to the recording so words spoken straight after
"hey jarvis" aren't clipped."""

import wave
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import voice as voice_module


@pytest.fixture
def voice(monkeypatch):
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)
    monkeypatch.setenv("WAKE_PREROLL_MS", "600")
    return voice_module.VoiceInterface(enable_tts=True, wake_backend=MagicMock())


def _chunk(value: int, voice) -> bytes:
    return np.full(voice.CHUNK, value, dtype=np.int16).tobytes()


def test_wake_keeps_last_600ms_of_detector_audio(voice, monkeypatch):
    chunks = [_chunk(i, voice) for i in range(20)]
    stream = MagicMock()
    stream.read.side_effect = chunks
    audio = MagicMock()
    audio.open.return_value = stream
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", lambda: audio)
    voice.wake_backend.detect.side_effect = [False] * 14 + [True]

    assert voice.wait_for_wake_word() is True

    # 600ms / 64ms chunks -> 10 chunks, ending with the one that fired.
    assert voice._wake_preroll == b"".join(chunks[5:15])
    assert voice._shared_stream is stream


def test_recording_starts_with_preroll_not_fed_to_vad(voice):
    preroll = _chunk(7, voice) * 3
    live = [_chunk(1000, voice)] * 2 + [_chunk(0, voice)] * 20
    stream = MagicMock()
    stream.read.side_effect = live
    audio = MagicMock()
    audio.get_sample_size.return_value = 2
    voice._shared_audio, voice._shared_stream, voice._wake_preroll = audio, stream, preroll
    vad = MagicMock()
    vad.is_speech.side_effect = [True, True] + [False] * 20
    voice.vad_backend = vad
    voice.vad_min_silence_ms = 300

    path = voice._record_until_silence()

    with wave.open(path, "rb") as wf:
        data = wf.readframes(wf.getnframes())
    assert data.startswith(preroll + _chunk(1000, voice))
    assert voice._used_preroll is True
    assert voice._wake_preroll == b""  # consumed once
    # VAD only ever saw live chunks.
    assert all(len(c.args[0]) == voice.CHUNK for c in vad.is_speech.call_args_list)
    assert vad.is_speech.call_count <= len(live)


@pytest.mark.parametrize("heard, expected", [
    ("Hey Jarvis, what's the weather today?", "what's the weather today?"),
    ("Jarvis. What's the weather?", "What's the weather?"),
    ("hey jarvis play some edm", "play some edm"),
    ("What's the weather, Jarvis?", "What's the weather, Jarvis?"),
    ("Jarvisville is a town", "Jarvisville is a town"),
])
def test_strip_wake_phrase(voice, heard, expected):
    assert voice._strip_wake_phrase(heard) == expected


def test_listen_strips_only_when_preroll_used(voice, monkeypatch):
    monkeypatch.setattr(voice, "_transcribe", lambda path: "Hey Jarvis, play some edm")

    def record(used):
        def _rec(**kw):
            voice._used_preroll = used
            return "/nonexistent.wav"
        return _rec

    monkeypatch.setattr(voice, "_record_until_silence", record(True))
    assert voice.listen() == "play some edm"
    monkeypatch.setattr(voice, "_record_until_silence", record(False))
    assert voice.listen() == "Hey Jarvis, play some edm"


class _FakeSession:
    def __init__(self, text="what's the weather"):
        self.text, self.pushed, self.aborted = text, [], False

    def push(self, pcm):
        self.pushed.append(pcm)

    def finish(self):
        return self.text

    def abort(self):
        self.aborted = True


def _record_with_session(voice, session, speech):
    voice._streaming_stt = MagicMock(start=MagicMock(return_value=session))
    stream = MagicMock()
    stream.read.side_effect = [_chunk(1000, voice)] * 2 + [_chunk(0, voice)] * 20
    audio = MagicMock()
    audio.get_sample_size.return_value = 2
    voice._shared_audio, voice._shared_stream, voice._wake_preroll = audio, stream, _chunk(7, voice)
    vad = MagicMock()
    vad.is_speech.side_effect = ([True, True] if speech else [False, False]) + [False] * 20
    voice.vad_backend, voice.vad_min_silence_ms = vad, 300
    return voice._record_until_silence(max_wait_seconds=0 if speech else 0.5)


def test_streaming_session_gets_preroll_and_live_chunks(voice):
    session = _FakeSession()
    _record_with_session(voice, session, speech=True)
    assert session.pushed[0] == _chunk(7, voice)          # pre-roll first
    assert session.pushed[1] == _chunk(1000, voice)       # then live mic audio
    assert voice._stream_session is session and not session.aborted


def test_streaming_session_aborted_when_no_speech(voice):
    session = _FakeSession()
    _record_with_session(voice, session, speech=False)
    assert session.aborted and voice._stream_session is None


def test_listen_uses_streamed_text_and_skips_whisper(voice, monkeypatch):
    session = _FakeSession("Hey Jarvis, what's the weather")
    whisper = MagicMock(return_value="whisper text")
    monkeypatch.setattr(voice, "_transcribe", whisper)

    def rec(**kw):
        voice._stream_session, voice._used_preroll = session, True
        return "/nonexistent.wav"

    monkeypatch.setattr(voice, "_record_until_silence", rec)
    assert voice.listen() == "what's the weather"
    whisper.assert_not_called()


def test_listen_falls_back_to_whisper_when_streaming_fails(voice, monkeypatch):
    monkeypatch.setattr(voice, "_transcribe", lambda path: "whisper text")

    def rec(**kw):
        voice._stream_session, voice._used_preroll = _FakeSession(text=None), False
        return "/nonexistent.wav"

    monkeypatch.setattr(voice, "_record_until_silence", rec)
    assert voice.listen() == "whisper text"
