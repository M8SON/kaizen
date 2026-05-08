"""Tests for KokoroTTSBackend.speak_stream — first-sentence-then-batch strategy."""

import unittest
from unittest.mock import MagicMock, patch


class SpeakStreamTests(unittest.TestCase):
    def _make_backend(self):
        from core import voice_backends

        with patch.object(voice_backends, "KPipeline"):
            backend = voice_backends.KokoroTTSBackend()
        # Replace the pipeline with a mock that returns an empty iterable
        # for every call — we only count flushes, not synthesise audio.
        backend.pipeline = MagicMock()
        backend.pipeline.side_effect = lambda *a, **k: iter([])
        return backend

    @patch("core.voice_backends.sd")
    def test_first_sentence_then_batch_remaining(self, mock_sd):
        """Stream first sentence; batch everything else as one flush.

        On Pi 5 each Kokoro call has ~6s of fixed overhead, so per-sentence
        flushing of a 4-sentence reply spent 36s with 3 audible gaps
        between sentences. New strategy: stream sentence 1 (perceived
        latency win), batch sentences 2-N as one synthesis call (one gap,
        amortised overhead).
        """
        backend = self._make_backend()
        chunks = iter(["Hello", " world", ".", " Two", ".", " Three", "."])
        backend.speak_stream(chunks)

        # Exactly two flushes: "Hello world." then " Two. Three."
        self.assertEqual(backend.pipeline.call_count, 2)
        first_call = backend.pipeline.call_args_list[0].args[0]
        second_call = backend.pipeline.call_args_list[1].args[0]
        self.assertEqual(first_call, "Hello world.")
        self.assertIn("Two", second_call)
        self.assertIn("Three", second_call)

    @patch("core.voice_backends.sd")
    def test_question_terminator_triggers_first_flush(self, mock_sd):
        backend = self._make_backend()
        chunks = iter(["Are you sure", "?", " Yes", "."])
        backend.speak_stream(chunks)

        self.assertEqual(backend.pipeline.call_count, 2)
        self.assertEqual(backend.pipeline.call_args_list[0].args[0], "Are you sure?")

    @patch("core.voice_backends.sd")
    def test_buffer_cap_triggers_first_flush_without_terminator(self, mock_sd):
        """If we hit BUFFER_CAP chars without a sentence terminator, flush
        anyway so the user isn't waiting forever for the first audio."""
        backend = self._make_backend()
        # 250 chars, no sentence boundary — cap is 200, leaves 50 for trailing flush.
        long = "a" * 250
        backend.speak_stream(iter([long]))

        self.assertEqual(backend.pipeline.call_count, 2)
        # First flush is exactly BUFFER_CAP chars; second is the remainder
        from core.voice_backends import KokoroTTSBackend
        self.assertEqual(
            len(backend.pipeline.call_args_list[0].args[0]),
            KokoroTTSBackend.BUFFER_CAP,
        )

    @patch("core.voice_backends.sd")
    def test_single_sentence_response_one_flush(self, mock_sd):
        """If the LLM only emits one sentence, only one Kokoro call should fire."""
        backend = self._make_backend()
        chunks = iter(["Just one sentence", "."])
        backend.speak_stream(chunks)
        self.assertEqual(backend.pipeline.call_count, 1)

    @patch("core.voice_backends.sd")
    def test_no_flush_on_empty_input(self, mock_sd):
        backend = self._make_backend()
        backend.speak_stream(iter([]))
        self.assertEqual(backend.pipeline.call_count, 0)

    @patch("core.voice_backends.sd")
    def test_no_flush_on_whitespace_only(self, mock_sd):
        backend = self._make_backend()
        backend.speak_stream(iter(["   ", "\n\n"]))
        # Whitespace contains \n which is a SENTENCE_TERMINATOR — but the
        # _flush helper bails on text.strip() == "". Should be zero pipeline calls.
        self.assertEqual(backend.pipeline.call_count, 0)

    @patch("core.voice_backends.sd")
    def test_first_audio_log_emitted(self, mock_sd):
        backend = self._make_backend()
        with self.assertLogs("core.voice_backends", level="INFO") as captured:
            backend.speak_stream(iter(["Hello.", " World."]))

        msgs = [r.getMessage() for r in captured.records]
        self.assertTrue(
            any("Kokoro TTS stream" in m for m in msgs),
            f"expected a stream timing log, got: {msgs}",
        )


if __name__ == "__main__":
    unittest.main()
