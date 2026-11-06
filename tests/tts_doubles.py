"""Test double for StreamingTTSBackend: synthesis delegates to a mock
`pipeline` yielding (graphemes, phonemes, audio) tuples, so tests can count
flushes and control the audio without loading a model."""

from unittest.mock import MagicMock

from core.voice_backends import StreamingTTSBackend


class PipelineTTSBackend(StreamingTTSBackend):
    def __init__(self, voice: str = "af_heart", speed: float = 1.0):
        super().__init__(voice, speed)
        self.pipeline = MagicMock()

    def _synth_audio(self, text: str):
        for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
            yield audio


def elevenlabs_client(chunks=None, error=None, credits="3"):
    """MagicMock ElevenLabs client whose
    `text_to_speech.with_raw_response.stream(...)` is a context manager
    yielding a response with `headers` and a `data` chunk iterator.

    `chunks` may be a list or a zero-arg callable returning an iterator (for
    streams that fail part-way); `error` raises on the call itself. Returns
    (client, stream_mock); stream_mock records calls like the real method."""
    import contextlib
    from types import SimpleNamespace

    state = {"chunks": chunks, "error": error}

    @contextlib.contextmanager
    def _stream(**kwargs):
        if state["error"] is not None:
            raise state["error"]
        src = state["chunks"]
        data = src() if callable(src) else (src if src is not None else [])
        try:
            yield SimpleNamespace(headers={"character-cost": credits}, data=data)
        finally:
            # Like the SDK, leaving the context closes the HTTP stream.
            close = getattr(data, "close", None)
            if callable(close):
                close()

    client = MagicMock()
    stream = client.text_to_speech.with_raw_response.stream
    stream.side_effect = _stream
    stream.state = state
    return client, stream
