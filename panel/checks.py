"""Test buttons: one minimal billed call per service, proving a key works.

Each check returns (ok, message). Messages are scrubbed of the key before
they reach the browser or a log. Calls are logged to the usage log with
purpose "panel-test", like any other billed call.
"""

from __future__ import annotations

import asyncio
import json

from core import usage_log
from panel.env_store import scrub

TIMEOUT_S = 15


def _friendly(exc: Exception, key: str) -> str:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    # ElevenLabs' ApiError str() is mostly response headers; its body has the reason.
    body = getattr(exc, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("message"):
        text = scrub(str(detail["message"]), [key])
    else:
        text = scrub(str(exc), [key])
    lowered = text.lower()
    if status == 401 or any(w in lowered for w in ("invalid api key", "unauthorized", "authenticat", "401")):
        hint = "Invalid key"
    elif status == 403 or "permission" in lowered:
        hint = "The key is missing a permission"
    elif status == 429 or "quota" in lowered or "rate limit" in lowered:
        hint = "Quota or rate limit reached"
    elif status == 404:
        hint = "Not found (check the model or voice)"
    else:
        hint = type(exc).__name__
    return f"{hint}: {text[:240]}" if text else hint


def check_claude(key: str, model: str) -> tuple[bool, str]:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_S, max_retries=0)
        response = client.messages.create(model=model, max_tokens=1,
                                          messages=[{"role": "user", "content": "hi"}])
        usage_log.record_claude(model, getattr(response, "usage", None), "panel-test")
        return True, f"Claude answered on {model}"
    except Exception as exc:  # noqa: BLE001
        return False, _friendly(exc, key)


def check_elevenlabs(key: str, voice_id: str, model_id: str) -> tuple[bool, str]:
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=key, timeout=TIMEOUT_S)
        with client.text_to_speech.with_raw_response.stream(
            voice_id=voice_id, text=".", model_id=model_id, output_format="pcm_24000",
        ) as response:
            usage_log.record("elevenlabs", model_id, "panel-test",
                             credits=(response.headers or {}).get("character-cost", 0))
            for chunk in response.data:
                if chunk:
                    break
        return True, "ElevenLabs spoke one character"
    except Exception as exc:  # noqa: BLE001
        return False, _friendly(exc, key)


def check_jev(key: str) -> tuple[bool, str]:
    try:
        from typesafe_sdk import Choice, TypeSafeClient
        response = TypeSafeClient(api_key=key).system_one(
            state={"transcript": "what's the weather"},
            questions={"q": Choice(instructions="Which topic is this?",
                                   criteria={"weather": "Weather", "other": "Anything else"})},
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            usage_log.record("jev", getattr(response, "model", None), "panel-test",
                             input_tokens=getattr(usage, "input_tokens", 0),
                             output_tokens=getattr(usage, "output_tokens", 0))
        response.choices["q"]
        return True, "Jev answered"
    except Exception as exc:  # noqa: BLE001
        return False, _friendly(exc, key)


async def _meta_handshake(key: str) -> None:
    import websockets
    from core.meta_stt import DEFAULT_MODEL, DEFAULT_URL
    handshake = {"mode": "PUSH_TO_TALK", "authorization": {"accessToken": key},
                 "audioEncoding": "PCM_16KHZ", "model": DEFAULT_MODEL,
                 "partialMode": "CUMULATIVE", "emitAudioProgress": False}
    async with websockets.connect(DEFAULT_URL, open_timeout=TIMEOUT_S) as ws:
        await ws.send(json.dumps(handshake))
        ack = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUT_S))
        if ack.get("type") == "error":
            raise RuntimeError(ack.get("message", "handshake rejected"))
        await ws.send(json.dumps({"type": "endStream"}))


def check_meta(key: str) -> tuple[bool, str]:
    try:
        asyncio.run(_meta_handshake(key))
        return True, "Meta accepted the key (no audio sent)"
    except Exception as exc:  # noqa: BLE001
        return False, _friendly(exc, key)
