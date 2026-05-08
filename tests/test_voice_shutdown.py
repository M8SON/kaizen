"""Tests for VoiceInterface.shutdown — the SIGINT/SIGTERM cleanup path."""

import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import voice as voice_module


@pytest.fixture
def fake_wav(tmp_path: Path) -> Path:
    path = tmp_path / "elevator.wav"
    samples = (np.zeros(4410, dtype=np.int16)).tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(samples)
    return path


@pytest.fixture
def voice(monkeypatch, fake_wav):
    monkeypatch.setattr(voice_module, "_MUSIC_ASSET_PATH", fake_wav)
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "false")
    return voice_module.VoiceInterface(enable_tts=True, wake_backend=MagicMock())


def test_shutdown_with_no_active_resources_is_noop(voice):
    voice.shutdown()  # must not raise
    assert voice._active_audio is None
    assert voice._active_stream is None
    assert voice._shared_audio is None
    assert voice._shared_stream is None


def test_shutdown_closes_active_pyaudio(voice):
    audio = MagicMock()
    stream = MagicMock()
    voice._active_audio = audio
    voice._active_stream = stream

    voice.shutdown()

    stream.stop_stream.assert_called_once()
    stream.close.assert_called_once()
    audio.terminate.assert_called_once()
    assert voice._active_audio is None
    assert voice._active_stream is None


def test_shutdown_closes_shared_pyaudio(voice):
    audio = MagicMock()
    stream = MagicMock()
    voice._shared_audio = audio
    voice._shared_stream = stream

    voice.shutdown()

    stream.stop_stream.assert_called_once()
    stream.close.assert_called_once()
    audio.terminate.assert_called_once()
    assert voice._shared_audio is None
    assert voice._shared_stream is None


def test_shutdown_swallows_pyaudio_errors(voice):
    """Shutdown runs from signal handlers — exceptions must not escape."""
    audio = MagicMock()
    stream = MagicMock()
    stream.stop_stream.side_effect = OSError("dev gone")
    stream.close.side_effect = OSError("close failed")
    audio.terminate.side_effect = OSError("term failed")
    voice._active_audio = audio
    voice._active_stream = stream

    voice.shutdown()  # must not raise

    assert voice._active_audio is None
    assert voice._active_stream is None


def test_shutdown_is_idempotent(voice):
    audio = MagicMock()
    stream = MagicMock()
    voice._active_audio = audio
    voice._active_stream = stream

    voice.shutdown()
    voice.shutdown()  # second call must not raise either

    stream.close.assert_called_once()
    audio.terminate.assert_called_once()


def test_shutdown_stops_music_thread(voice):
    voice._music_playing = True
    voice._music_thread = MagicMock()
    voice._music_thread.is_alive.return_value = True

    voice.shutdown()

    assert voice._music_playing is False
    voice._music_thread is None  # cleared by stop_thinking_music
