"""Unit tests for the elevator-music feature on core.voice.VoiceInterface."""

import logging
import threading
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import sounddevice as sd

from core import voice as voice_module


@pytest.fixture
def fake_wav(tmp_path: Path) -> Path:
    """Write a 0.1-second 16-bit mono 44.1kHz WAV for tests."""
    path = tmp_path / "elevator.wav"
    samples = (np.zeros(4410, dtype=np.int16)).tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(samples)
    return path


@pytest.fixture
def voice_factory(monkeypatch, fake_wav):
    """Build a Voice instance with audio output safely stubbed.

    Stubs:
      - resolve_input_device / resolve_output_device → fake indices
      - output_samplerate → 48000
      - WhisperBackend / KokoroTTSBackend → MagicMock so init doesn't
        try to load real models
      - pyaudio.PyAudio → MagicMock so no microphone is opened
    """
    monkeypatch.setattr(voice_module, "_MUSIC_ASSET_PATH", fake_wav)
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)

    def _make(**overrides):
        kwargs = dict(enable_tts=True)
        kwargs.update(overrides)
        return voice_module.VoiceInterface(**kwargs)

    return _make


def test_disabled_by_env_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "false")
    sd_play = MagicMock()
    monkeypatch.setattr(sd, "play", sd_play)
    v = voice_factory()
    v.start_thinking_music()
    assert v._music_thread is None
    assert sd_play.call_count == 0


def test_tts_disabled_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    sd_play = MagicMock()
    monkeypatch.setattr(sd, "play", sd_play)
    v = voice_factory(enable_tts=False)
    v.start_thinking_music()
    assert v._music_thread is None
    assert sd_play.call_count == 0


def test_missing_asset_logs_and_disables(monkeypatch, voice_factory, caplog, tmp_path):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    monkeypatch.setattr(voice_module, "_MUSIC_ASSET_PATH", tmp_path / "nope.wav")
    with caplog.at_level(logging.WARNING, logger="core.voice"):
        v = voice_factory()
    assert v._music_buffer is None
    assert any("elevator" in rec.message.lower() for rec in caplog.records)
    # start is a safe no-op
    v.start_thinking_music()
    assert v._music_thread is None


def test_start_then_stop_no_raise(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    play_event = threading.Event()
    stop_called = threading.Event()

    def fake_play(*args, **kwargs):
        play_event.set()

    def fake_wait():
        # Block until sd.stop() is called or the test times out.
        stop_called.wait(timeout=1.0)

    def fake_stop():
        stop_called.set()

    monkeypatch.setattr(sd, "play", fake_play)
    monkeypatch.setattr(sd, "wait", fake_wait)
    monkeypatch.setattr(sd, "stop", fake_stop)

    v = voice_factory()
    v.start_thinking_music()
    assert play_event.wait(timeout=1.0), "sd.play was never called"
    v.stop_thinking_music()
    # Thread must have exited.
    assert v._music_thread is None or not v._music_thread.is_alive()


def test_stop_without_start_is_noop(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    sd_stop = MagicMock()
    monkeypatch.setattr(sd, "stop", sd_stop)
    v = voice_factory()
    v.stop_thinking_music()  # never started
    assert sd_stop.call_count == 0


def test_double_start_only_spawns_one_thread(monkeypatch, voice_factory):
    monkeypatch.setenv("MINICLAW_ELEVATOR_MUSIC", "true")
    stop_called = threading.Event()

    monkeypatch.setattr(sd, "play", lambda *a, **kw: None)
    monkeypatch.setattr(sd, "wait", lambda: stop_called.wait(timeout=1.0))
    monkeypatch.setattr(sd, "stop", lambda: stop_called.set())

    v = voice_factory()
    v.start_thinking_music()
    first = v._music_thread
    v.start_thinking_music()
    assert v._music_thread is first
    v.stop_thinking_music()
