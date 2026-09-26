"""Tests for the looping R2-D2 pre-buffer cue."""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice as voice_mod
from core.voice import VoiceInterface
from core.voice_backends import KOKORO_SAMPLE_RATE


def _make_voice():
    v = VoiceInterface.__new__(VoiceInterface)
    v.enable_tts = True
    v._output_samplerate = KOKORO_SAMPLE_RATE
    v._output_device_index = None
    v._prebuffer_cue = None
    v._prebuffer_cue_lock = threading.Lock()
    return v


def test_segment_is_nonempty_float32():
    v = _make_voice()
    seg = v._prebuffer_cue_segment()
    assert isinstance(seg, np.ndarray)
    assert seg.dtype == np.float32
    assert len(seg) > int(0.3 * v._output_samplerate)  # ~0.44s segment


def test_start_then_stop_lifecycle():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is not None
        _, thread = v._prebuffer_cue
        v.stop_prebuffer_cue()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert v._prebuffer_cue is None


def test_stop_without_start_is_noop():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.stop_prebuffer_cue()  # must not raise
        assert v._prebuffer_cue is None


def test_double_start_does_not_spawn_two():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        first = v._prebuffer_cue
        v.start_prebuffer_cue()  # idempotent
        assert v._prebuffer_cue is first
        v.stop_prebuffer_cue()


def test_noop_when_tts_disabled():
    v = _make_voice()
    v.enable_tts = False
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is None


def test_quiet_points_find_gaps_between_bloops():
    v = _make_voice()
    seg = v._prebuffer_cue_segment()
    quiet = voice_mod._quiet_points(seg, int(0.01 * v._output_samplerate))
    assert quiet[0] == 0 and quiet[-1] == len(seg)
    assert len(quiet) >= 5  # 0, three inter-bloop gaps, end
    for q in quiet[1:-1]:
        assert np.all(seg[q : q + 10] == 0)


def test_stop_mid_bloop_finishes_to_next_gap():
    """Stopping mid-sound lets the current bloop finish instead of chopping
    it, and never plays more than that one element."""
    v = _make_voice()
    seg = v._prebuffer_cue_segment()
    written = []

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, block):
            written.append(np.array(block))
            if len(written) == 3:  # mid-way through the first chirp region
                v.stop_prebuffer_cue()

    with patch.object(voice_mod.sd, "OutputStream", return_value=FakeStream()):
        v.start_prebuffer_cue()
        for t in threading.enumerate():
            if t.name == "prebuffer-cue":
                t.join(timeout=2)

    played = np.concatenate(written)
    quiet = voice_mod._quiet_points(seg, int(0.01 * v._output_samplerate))
    assert len(played) in set(quiet.tolist())  # ended exactly at a rest point
    assert len(played) - 3 * 1024 <= int(0.2 * v._output_samplerate)  # <= one element
