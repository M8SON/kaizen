"""ElevenLabs failing mid-session (quota, network) falls back to local Kokoro
instead of going silent, then retries ElevenLabs after a while."""

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from core import voice_backends
from core.voice_backends import ElevenLabsTTSBackend, elevenlabs_self_check


def _pcm(n=4):
    return (np.ones(n, dtype="<i2") * 1000).tobytes()


class _Local:
    def __init__(self):
        self.calls = []

    def _synth_audio(self, text):
        self.calls.append(text)
        yield np.zeros(8, dtype=np.float32)


def _backend(stream_side_effect, local=None):
    client = MagicMock()
    client.text_to_speech.stream.side_effect = stream_side_effect
    factory = MagicMock(return_value=local) if local is not None else None
    return ElevenLabsTTSBackend(voice_id="v", api_key="k", client=client, fallback_factory=factory), client, factory


class RuntimeFallbackTests(unittest.TestCase):
    def test_failure_before_audio_uses_local_backend(self):
        local = _Local()
        b, client, factory = _backend(RuntimeError("quota_exceeded"), local)
        audio = list(b._synth_audio("Hello there."))
        self.assertEqual(len(audio), 1)
        self.assertEqual(local.calls, ["Hello there."])

    def test_stays_local_during_retry_window_then_retries(self):
        local = _Local()
        b, client, factory = _backend(RuntimeError("quota_exceeded"), local)
        list(b._synth_audio("one"))
        list(b._synth_audio("two"))
        self.assertEqual(client.text_to_speech.stream.call_count, 1)
        self.assertEqual(factory.call_count, 1)  # built once, reused
        b._fallback_until = 0.0  # window elapsed
        client.text_to_speech.stream.side_effect = None
        client.text_to_speech.stream.return_value = iter([_pcm()])
        audio = list(b._synth_audio("three"))
        self.assertEqual(client.text_to_speech.stream.call_count, 2)
        self.assertEqual(local.calls, ["one", "two"])
        self.assertEqual(len(audio), 1)

    def test_failure_after_partial_audio_is_not_repeated_locally(self):
        def broken_stream(**kw):
            yield _pcm()
            raise RuntimeError("connection reset")
        local = _Local()
        b, client, _ = _backend(lambda **kw: broken_stream(), local)
        with self.assertRaises(RuntimeError):
            list(b._synth_audio("Hello."))
        self.assertEqual(local.calls, [])

    def test_no_fallback_configured_reraises(self):
        b, _, _ = _backend(RuntimeError("down"))
        with self.assertRaises(RuntimeError):
            list(b._synth_audio("Hello."))

    def test_self_check_still_fails_when_elevenlabs_down(self):
        b, _, _ = _backend(RuntimeError("down"), _Local())
        with self.assertRaises(RuntimeError):
            elevenlabs_self_check(b)


if __name__ == "__main__":
    unittest.main()
