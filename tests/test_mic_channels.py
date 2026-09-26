"""2-channel mic capture (XVF3800): channel 1 is primary for recording/STT,
the wake word fires on either channel."""

import wave
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import voice as voice_module


@pytest.fixture
def stereo_voice(monkeypatch):
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "input_channel_count", lambda *a, **k: 2)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)
    monkeypatch.delenv("MIC_CHANNEL", raising=False)
    return voice_module.VoiceInterface(
        enable_tts=True, wake_backend=MagicMock(), wake_backend_alt=MagicMock()
    )


def _stereo(ch0: int, ch1: int, n=1024) -> bytes:
    return np.column_stack([np.full(n, ch0, np.int16), np.full(n, ch1, np.int16)]).tobytes()


def test_primary_is_channel_1_and_split_deinterleaves(stereo_voice):
    assert stereo_voice._primary_channel == 1
    ch0, ch1 = stereo_voice._split_channels(_stereo(5, 9))
    assert (ch0 == 5).all() and (ch1 == 9).all()


def test_mic_channel_env_overrides_primary(monkeypatch, stereo_voice):
    monkeypatch.setenv("MIC_CHANNEL", "0")
    v = voice_module.VoiceInterface(enable_tts=True, wake_backend=MagicMock(), wake_backend_alt=MagicMock())
    assert v._primary_channel == 0


def test_wake_fires_on_either_channel_and_feeds_both(stereo_voice):
    stereo_voice.wake_backend.detect.return_value = False
    stereo_voice.wake_backend_alt.detect.return_value = True
    assert stereo_voice._wake_detected(stereo_voice._split_channels(_stereo(5, 9))) is True
    # primary detector got channel 1, alt got channel 0 — both fed every chunk
    assert (stereo_voice.wake_backend.detect.call_args.args[0] == 9).all()
    assert (stereo_voice.wake_backend_alt.detect.call_args.args[0] == 5).all()


def test_wake_loop_prerolls_primary_channel_only(stereo_voice, monkeypatch):
    stream = MagicMock()
    stream.read.side_effect = [_stereo(1, 2), _stereo(3, 4)]
    audio = MagicMock()
    audio.open.return_value = stream
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", lambda: audio)
    stereo_voice.wake_backend.detect.side_effect = [False, True]
    stereo_voice.wake_backend_alt.detect.return_value = False

    assert stereo_voice.wait_for_wake_word() is True
    assert audio.open.call_args.kwargs["channels"] == 2
    assert stereo_voice._wake_preroll == (
        np.full(1024, 2, np.int16).tobytes() + np.full(1024, 4, np.int16).tobytes()
    )


def test_recording_writes_mono_primary_channel(stereo_voice):
    stream = MagicMock()
    stream.read.side_effect = [_stereo(111, 222)] * 2 + [_stereo(0, 0)] * 20
    audio = MagicMock()
    audio.get_sample_size.return_value = 2
    stereo_voice._shared_audio, stereo_voice._shared_stream = audio, stream
    vad = MagicMock()
    vad.is_speech.side_effect = [True, True] + [False] * 20
    stereo_voice.vad_backend, stereo_voice.vad_min_silence_ms = vad, 300

    path = stereo_voice._record_until_silence()

    with wave.open(path, "rb") as wf:
        assert wf.getnchannels() == 1
        data = np.frombuffer(wf.readframes(wf.getnframes()), np.int16)
    assert (data[:2048] == 222).all()  # channel 1 only
    assert (vad.is_speech.call_args_list[0].args[0] == 222).all()


def test_mono_mic_ignores_alt_backend(monkeypatch):
    monkeypatch.setattr(voice_module, "resolve_input_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "resolve_output_device", lambda *a, **k: 0)
    monkeypatch.setattr(voice_module, "output_samplerate", lambda *a, **k: 48000)
    monkeypatch.setattr(voice_module, "input_channel_count", lambda *a, **k: 1)
    monkeypatch.setattr(voice_module, "WhisperBackend", MagicMock)
    monkeypatch.setattr(voice_module, "KokoroTTSBackend", MagicMock)
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", MagicMock)
    v = voice_module.VoiceInterface(enable_tts=True, wake_backend=MagicMock(), wake_backend_alt=MagicMock())
    assert v._primary_channel == 0 and v.wake_backend_alt is None
    assert len(v._split_channels(np.zeros(1024, np.int16).tobytes())) == 1


def test_near_miss_is_logged_with_per_channel_peaks(stereo_voice, caplog):
    import logging
    diag = voice_module._WakeDiagnostics(stereo_voice)
    stereo_voice.wake_backend.threshold = 0.7
    chans = stereo_voice._split_channels(_stereo(0, 0))
    with caplog.at_level(logging.INFO, logger="core.voice"):
        for primary, alt in [(0.3, 0.1), (0.55, 0.2), (0.05, 0.02)]:  # rise, peak, fall — never fires
            stereo_voice.wake_backend.last_score = primary   # channel 1
            stereo_voice.wake_backend_alt.last_score = alt   # channel 0
            diag.observe(chans, detected=False)
    assert "Wake near-miss: peak score ch0=0.20 ch1=0.55 (threshold 0.70)" in caplog.text


def test_detection_is_not_logged_as_near_miss(stereo_voice, caplog):
    import logging
    diag = voice_module._WakeDiagnostics(stereo_voice)
    chans = stereo_voice._split_channels(_stereo(0, 0))
    with caplog.at_level(logging.INFO, logger="core.voice"):
        stereo_voice.wake_backend.last_score, stereo_voice.wake_backend_alt.last_score = 0.9, 0.3
        diag.observe(chans, detected=True)
        stereo_voice.wake_backend.last_score, stereo_voice.wake_backend_alt.last_score = 0.0, 0.0
        diag.observe(chans, detected=False)
    assert "near-miss" not in caplog.text


def _wake_run(voice, monkeypatch, primary_scores, alt_scores):
    stream = MagicMock()
    stream.read.side_effect = [_stereo(0, 0)] * len(primary_scores)
    audio = MagicMock()
    audio.open.return_value = stream
    monkeypatch.setattr(voice_module.pyaudio, "PyAudio", lambda: audio)
    it_p, it_a = iter(primary_scores), iter(alt_scores)

    def det_p(_):
        voice.wake_backend.last_score = next(it_p)
        return voice.wake_backend.last_score >= 0.7

    def det_a(_):
        voice.wake_backend_alt.last_score = next(it_a)
        return voice.wake_backend_alt.last_score >= 0.7

    voice.wake_backend.detect.side_effect = det_p
    voice.wake_backend_alt.detect.side_effect = det_a
    return voice.wait_for_wake_word()


def test_soft_wake_fires_between_soft_and_hard_threshold(stereo_voice, monkeypatch):
    stereo_voice._wake_soft_threshold = 0.25
    assert _wake_run(stereo_voice, monkeypatch, [0.05, 0.1, 0.3], [0.0, 0.2, 0.4]) is True
    assert stereo_voice._soft_wake is True


def test_hard_wake_is_not_soft(stereo_voice, monkeypatch):
    stereo_voice._wake_soft_threshold = 0.25
    assert _wake_run(stereo_voice, monkeypatch, [0.1, 0.9], [0.0, 0.1]) is True
    assert stereo_voice._soft_wake is False


def test_soft_wake_disabled_by_default(stereo_voice, monkeypatch):
    assert stereo_voice._wake_soft_threshold == 0
    with pytest.raises(StopIteration):  # never fires on 0.4; runs out of audio
        _wake_run(stereo_voice, monkeypatch, [0.4, 0.4], [0.4, 0.4])


def _listen_after(voice, monkeypatch, soft, text):
    monkeypatch.setattr(voice, "_transcribe", lambda path: text)

    def rec(**kw):
        voice._heard_speech, voice._used_preroll, voice._stream_session = True, True, None
        return "/nonexistent.wav"

    monkeypatch.setattr(voice, "_record_until_silence", rec)
    voice._soft_wake = soft
    return voice.listen()


def test_soft_wake_confirmed_when_transcript_says_jarvis(stereo_voice, monkeypatch):
    assert _listen_after(stereo_voice, monkeypatch, True, "Jarvis, what time is it?") == "what time is it?"
    assert stereo_voice._soft_wake is False


def test_soft_wake_rejected_without_wake_name(stereo_voice, monkeypatch):
    assert _listen_after(stereo_voice, monkeypatch, True, "and then the TV said something") is None


def test_hard_wake_needs_no_confirmation(stereo_voice, monkeypatch):
    assert _listen_after(stereo_voice, monkeypatch, False, "what time is it?") == "what time is it?"


def test_soft_wake_accepted_when_confirmer_says_addressed(stereo_voice, monkeypatch):
    stereo_voice.wake_confirmer = lambda text: True
    assert _listen_after(stereo_voice, monkeypatch, True, "Hey Jarrett, can you look up the news?") \
        == "Hey Jarrett, can you look up the news?"


def test_soft_wake_rejected_when_confirmer_says_background(stereo_voice, monkeypatch):
    stereo_voice.wake_confirmer = lambda text: False
    assert _listen_after(stereo_voice, monkeypatch, True, "ain't no helping you") is None


def test_soft_wake_recording_is_capped(stereo_voice, monkeypatch):
    """A soft wake during music must not record lyrics indefinitely."""
    monkeypatch.setattr(voice_module, "SOFT_WAKE_MAX_RECORD_S", 0.5)
    stream = MagicMock()
    stream.read.side_effect = [_stereo(500, 500)] * 100          # continuous "vocals"
    audio = MagicMock()
    audio.get_sample_size.return_value = 2
    stereo_voice._shared_audio, stereo_voice._shared_stream = audio, stream
    stereo_voice.vad_backend = MagicMock(is_speech=MagicMock(return_value=True))
    stereo_voice._soft_wake = True
    stereo_voice._record_until_silence()
    assert stream.read.call_count <= 10   # 0.5s = ~8 chunks, not all 100
