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
