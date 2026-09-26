"""
Streaming speech-to-text via Meta's Muse Voice Transcribe realtime API.

Audio is streamed while the user is still talking, so the final transcript
arrives ~60-120ms after speech ends (measured on the Pi) instead of the
1.5-3s a local Whisper pass takes afterwards.

Protocol (from Meta's cookbook, 06_muse_voice/01_voice_api_fundamentals):
JSON handshake carrying the key -> `{"sessionId"}` ack -> binary PCM frames ->
`{"type": "endStream"}` -> `{"type": "transcript", "final": true, ...}`.

Failure-tolerant by design: any connect/protocol/timeout problem makes
finish() return None, and the caller falls back to local Whisper on the WAV
it recorded anyway. Never raises to the caller.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading

logger = logging.getLogger(__name__)

DEFAULT_URL = "wss://api.meta.ai/v1/asr/realtime"
DEFAULT_MODEL = "muse-voice-transcribe-1.0"

_END = object()
_ABORT = object()


class MetaStreamingStt:
    """Factory for per-utterance streaming sessions (16kHz mono int16 PCM)."""

    def __init__(
        self,
        api_key: str,
        keywords: list[str] | None = None,
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        finish_timeout_s: float = 3.0,
    ):
        self.api_key = api_key
        self.keywords = list(keywords or [])
        self.url = url
        self.model = model
        self.finish_timeout_s = finish_timeout_s

    def start(self) -> "MetaSttSession":
        return MetaSttSession(self)


class MetaSttSession:
    """One utterance. Connects immediately in a background thread; push()
    buffers audio until the socket is ready (the server allows up to 5s of
    backlog, and the handshake takes ~0.4-1.3s)."""

    def __init__(self, config: MetaStreamingStt):
        self._config = config
        self._queue: asyncio.Queue = asyncio.Queue()
        self._loop = asyncio.new_event_loop()
        self._task = None
        self._result: concurrent.futures.Future = concurrent.futures.Future()
        self._closed = False
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="meta-stt")
        self._thread.start()

    def _thread_main(self) -> None:
        # The session's whole lifecycle runs on this thread: the task always
        # finishes (or is cancelled) and unwinds before the loop is closed.
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._run())
        try:
            self._result.set_result(self._loop.run_until_complete(self._task))
        except BaseException as exc:  # noqa: BLE001 - includes CancelledError
            self._result.set_exception(exc)
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def push(self, pcm: bytes) -> None:
        if not self._closed and pcm:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm)

    def finish(self) -> str | None:
        """End the utterance and wait for the final transcript (None on failure)."""
        if self._closed:
            return None
        self._closed = True
        self._loop.call_soon_threadsafe(self._queue.put_nowait, _END)
        try:
            return self._result.result(timeout=self._config.finish_timeout_s)
        except BaseException as exc:  # noqa: BLE001 - caller falls back to Whisper
            logger.warning("Meta STT failed (%s: %s) — falling back to local Whisper",
                           type(exc).__name__, exc)
            return None
        finally:
            self._stop()

    def abort(self) -> None:
        """Drop the utterance (no speech) without waiting for a transcript."""
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._queue.put_nowait, _ABORT)
        try:
            self._result.result(timeout=1.0)  # let the socket close cleanly
        except BaseException:  # noqa: BLE001
            pass
        self._stop()

    def _stop(self) -> None:
        if not self._result.done():
            try:
                self._loop.call_soon_threadsafe(self._cancel_task)
            except RuntimeError:  # loop already closed
                pass
        self._thread.join(timeout=2.0)

    def _cancel_task(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> str | None:
        import websockets

        cfg = self._config
        handshake = {
            "mode": "PUSH_TO_TALK",
            "authorization": {"accessToken": cfg.api_key},
            "audioEncoding": "PCM_16KHZ",
            "model": cfg.model,
            "partialMode": "CUMULATIVE",
            "emitAudioProgress": False,
        }
        if cfg.keywords:
            handshake["keywords"] = cfg.keywords

        async with websockets.connect(cfg.url, max_size=None) as ws:
            await ws.send(json.dumps(handshake))
            ack = json.loads(await ws.recv())
            if ack.get("type") == "error":
                raise RuntimeError(ack.get("message", "handshake rejected"))

            async def send_audio():
                while True:
                    item = await self._queue.get()
                    if item is _ABORT:
                        await ws.close()
                        return
                    if item is _END:
                        await ws.send(json.dumps({"type": "endStream"}))
                        return
                    await ws.send(item)

            sender = asyncio.create_task(send_audio())
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        continue
                    event = json.loads(message)
                    if event.get("type") == "error":
                        raise RuntimeError(event.get("message", "transcription error"))
                    if event.get("type") == "transcript" and event.get("final"):
                        return (event.get("transcript") or "").strip()
            finally:
                sender.cancel()
        return None
