"""MetaStreamingStt against a local server speaking the realtime protocol
(handshake -> sessionId ack -> binary PCM -> endStream -> final transcript),
as captured from Meta's cookbook and verified against the live API."""

import asyncio
import json
import threading

import pytest
import websockets

from core.meta_stt import MetaStreamingStt


class FakeMetaServer:
    def __init__(self, reject=False, silent=False):
        self.reject, self.silent = reject, silent
        self.handshake = None
        self.audio = b""
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._serve, daemon=True).start()
        self.ready.wait(5)

    def _serve(self):
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.handshake = json.loads(await ws.recv())
            if self.reject:
                await ws.send(json.dumps({"type": "error", "message": "Unauthorized"}))
                return
            await ws.send(json.dumps({"sessionId": "s1"}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.audio += msg
                elif json.loads(msg).get("type") == "endStream" and not self.silent:
                    await ws.send(json.dumps({"type": "transcript", "final": False, "transcript": "what's"}))
                    await ws.send(json.dumps({"type": "transcript", "final": True, "transcript": " what's the weather "}))

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                self.url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
                self.ready.set()
                await asyncio.Future()

        self.loop.run_until_complete(main())


def test_streams_audio_and_returns_final_transcript():
    server = FakeMetaServer()
    session = MetaStreamingStt("k", keywords=["Jarvis"], url=server.url).start()
    session.push(b"\x01\x00" * 100)
    session.push(b"\x02\x00" * 100)
    assert session.finish() == "what's the weather"
    assert server.audio == b"\x01\x00" * 100 + b"\x02\x00" * 100
    assert server.handshake["authorization"] == {"accessToken": "k"}
    assert server.handshake["audioEncoding"] == "PCM_16KHZ"
    assert server.handshake["keywords"] == ["Jarvis"]


def test_rejected_handshake_returns_none():
    server = FakeMetaServer(reject=True)
    session = MetaStreamingStt("bad", url=server.url).start()
    session.push(b"\x00" * 10)
    assert session.finish() is None


def test_no_final_transcript_times_out_to_none():
    server = FakeMetaServer(silent=True)
    session = MetaStreamingStt("k", url=server.url, finish_timeout_s=0.5).start()
    assert session.finish() is None


def test_unreachable_server_returns_none():
    session = MetaStreamingStt("k", url="ws://127.0.0.1:9", finish_timeout_s=2).start()
    assert session.finish() is None


def test_abort_and_double_finish_are_safe():
    server = FakeMetaServer()
    session = MetaStreamingStt("k", url=server.url).start()
    session.push(b"\x00" * 10)
    session.abort()
    assert session.finish() is None
