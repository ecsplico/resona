"""Deepgram-compatible live streaming transcription: ``WS /v1/listen``.

This endpoint mirrors Deepgram's live streaming API closely enough that the
official Deepgram SDK works against Resona unchanged for the core path:

  * Connect with query params (``model``, ``language``, ``encoding``,
    ``sample_rate``, ``channels``, ``interim_results``, ``punctuate``).
  * Authenticate with ``Authorization: Token <RESONA_API_KEY>`` (also accepts
    ``Bearer``, ``X-API-Key``, or ``?token=`` / ``?api_key=``).
  * Stream raw ``linear16`` PCM as binary WebSocket frames.
  * Receive ``Results`` (interim + final) and a closing ``Metadata`` message.

Resona extensions: ``engine`` (pick a specific local engine) and ``profile``
(apply a postprocessing profile to each final transcript).

The endpoint bridges the Deepgram wire protocol to one of two upstreams,
resolved via the gateway catalogue:

  * **local** engine-server — proxied to its ``/ws/live`` WebSocket.
  * **cloud** provider with a realtime API (Deepgram, ElevenLabs) — proxied to
    the provider's live STT WebSocket via ``resona_cloud_stt.streaming``.

Cloud providers without a streaming API (OpenAI) are rejected.
"""
import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone

import websockets
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from decouple import config

from resona_cloud_stt.streaming import open_stream, supports_streaming

from . import engine_registry as reg
from .paths import PROFILES_PATH

# How long to keep draining final transcripts after the client stops sending,
# for providers (e.g. ElevenLabs) that do not close the socket on commit.
_CLOUD_FINAL_DRAIN_SECONDS = 3.0

log = logging.getLogger(__name__)
router = APIRouter()

# Encodings that map directly to the engine's int16 PCM input.
_SUPPORTED_ENCODINGS = {"linear16", "pcm_s16le", "s16le", ""}


def _truthy(value: str | None, default: bool = False) -> bool:
    """Parse a Deepgram-style boolean query param."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _authorize(websocket: WebSocket) -> bool:
    """Validate the request against RESONA_API_KEY. Returns True if allowed.

    Auth is disabled (always allowed) when RESONA_API_KEY is unset.
    """
    expected = config("RESONA_API_KEY", default="")
    if not expected:
        return True

    auth = websocket.headers.get("authorization", "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() in ("token", "bearer") and token == expected:
            return True

    if websocket.headers.get("x-api-key") == expected:
        return True

    qp = websocket.query_params
    if qp.get("token") == expected or qp.get("api_key") == expected:
        return True

    return False


def _ws_url(http_url: str, language: str) -> str:
    """Convert an engine-server http(s) base URL to its /ws/live WebSocket URL."""
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/ws/live?language={language}"


def deepgram_results(
    transcript: str, *, is_final: bool, channels: int = 1
) -> dict:
    """Build a Deepgram-shaped ``Results`` message."""
    return {
        "type": "Results",
        "channel_index": [0, channels],
        "duration": 0.0,
        "start": 0.0,
        "is_final": is_final,
        "speech_final": is_final,
        "channel": {
            "alternatives": [
                {
                    "transcript": transcript,
                    "confidence": 1.0 if transcript else 0.0,
                    "words": [],
                }
            ]
        },
    }


def deepgram_metadata(request_id: str, *, models: list[str], channels: int = 1) -> dict:
    """Build a Deepgram-shaped ``Metadata`` message (sent at end of stream)."""
    return {
        "type": "Metadata",
        "transaction_key": "deprecated",
        "request_id": request_id,
        "sha256": "",
        "created": datetime.now(timezone.utc).isoformat(),
        "duration": 0.0,
        "channels": channels,
        "models": models,
    }


@router.websocket("/v1/listen")
async def listen(websocket: WebSocket):
    """Deepgram-compatible live transcription WebSocket."""
    if not _authorize(websocket):
        await websocket.close(code=1008)  # policy violation
        return

    qp = websocket.query_params
    language = qp.get("language") or "de"
    encoding = (qp.get("encoding") or "").lower()
    sample_rate = int(qp.get("sample_rate") or 16000)
    channels = int(qp.get("channels") or 1)
    interim_results = _truthy(qp.get("interim_results"), default=False)
    engine_name = qp.get("engine") or qp.get("model")
    profile_name = qp.get("profile")

    await websocket.accept()

    if encoding not in _SUPPORTED_ENCODINGS:
        await websocket.send_json({
            "type": "Error",
            "description": f"unsupported encoding '{encoding}' — only linear16 PCM is supported",
        })
        await websocket.close(code=1003)
        return

    # Resolve the engine, then bridge to whichever upstream supports streaming.
    try:
        info = reg.resolve(engine_name, "stt", private=False)
    except reg.EngineError as exc:
        await websocket.send_json({"type": "Error", "description": str(exc)})
        await websocket.close(code=1011)
        return

    pipeline = _build_profile_pipeline(profile_name)
    request_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    common = dict(
        sample_rate=sample_rate, channels=channels, interim_results=interim_results,
        pipeline=pipeline, request_id=request_id, models=info.models,
    )

    if info.kind == "local" and info.url:
        await _run_local_bridge(websocket, info, language=language, **common)
    elif info.kind == "cloud" and supports_streaming(info.provider or ""):
        await _run_cloud_bridge(websocket, info, language=language, loop=loop, **common)
    else:
        await websocket.send_json({
            "type": "Error",
            "description": f"engine '{info.name}' does not support live streaming "
                           "(no realtime API)",
        })
        await websocket.close(code=1003)


async def _run_local_bridge(websocket, info, *, language, sample_rate, channels,
                            interim_results, pipeline, request_id, models):
    """Proxy the client to a local engine-server ``/ws/live``."""
    loop = asyncio.get_event_loop()
    try:
        async with websockets.connect(_ws_url(info.url, language), max_size=None) as engine_ws:
            await _bridge(
                websocket, engine_ws, loop,
                sample_rate=sample_rate, channels=channels,
                interim_results=interim_results, pipeline=pipeline,
                request_id=request_id, models=models,
            )
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception as exc:  # engine unreachable, etc.
        log.error("streaming bridge failed: %s", exc, exc_info=True)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({"type": "Error", "description": str(exc)})
            except Exception:
                pass
    finally:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()


async def _run_cloud_bridge(websocket, info, *, language, loop, sample_rate, channels,
                            interim_results, pipeline, request_id, models):
    """Proxy the client to a cloud provider's realtime STT WebSocket."""
    try:
        api_key = reg.cloud_api_key(info.provider)
    except reg.EngineError as exc:
        await websocket.send_json({"type": "Error", "description": str(exc)})
        await websocket.close(code=1011)
        return

    model = info.models[0] if info.models else None
    try:
        session = await open_stream(
            info.provider, api_key=api_key, model=model, language=language,
            sample_rate=sample_rate, interim_results=interim_results,
        )
    except Exception as exc:
        log.error("cloud stream open failed (%s): %s", info.provider, exc, exc_info=True)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({
                    "type": "Error",
                    "description": f"failed to open {info.provider} stream: {exc}",
                })
            except Exception:
                pass
            await websocket.close(code=1011)
        return

    try:
        await _bridge_cloud(
            websocket, session, loop,
            channels=channels, interim_results=interim_results,
            pipeline=pipeline, request_id=request_id, models=models,
        )
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception as exc:
        log.error("cloud streaming bridge failed: %s", exc, exc_info=True)
    finally:
        await session.close()
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()


def _build_profile_pipeline(profile_name: str | None):
    """Resolve a profile into a runnable pipeline, or None when unspecified."""
    if not profile_name:
        return None
    from resona_postprocess.pipeline import build_pipeline
    from resona_postprocess.profile import resolve_profile, ProfileError
    try:
        prof = resolve_profile(profile_name, PROFILES_PATH)
    except ProfileError as exc:
        log.warning("ignoring invalid streaming profile '%s': %s", profile_name, exc)
        return None
    return build_pipeline(prof)


async def _bridge(
    client_ws: WebSocket,
    engine_ws,
    loop,
    *,
    sample_rate: int,
    channels: int,
    interim_results: bool,
    pipeline,
    request_id: str,
    models: list[str],
) -> None:
    """Pump audio client->engine and translated transcripts engine->client."""

    async def client_to_engine():
        try:
            while True:
                message = await client_ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await engine_ws.send(json.dumps({
                        "type": "audio",
                        "data": base64.b64encode(data).decode("ascii"),
                        "sample_rate": sample_rate,
                    }))
                    continue
                text = message.get("text")
                if text is None:
                    continue
                if not await _handle_control(text, engine_ws, sample_rate):
                    break  # CloseStream / Finalize -> stop reading client
        finally:
            # Tell the engine to flush whatever remains.
            try:
                await engine_ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass

    async def engine_to_client():
        async for raw in engine_ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")

            if mtype in ("partial", "final"):
                delta = (msg.get("delta") or "").strip()
                if delta:
                    transcript = await _postprocess(delta, pipeline, loop)
                    await client_ws.send_json(
                        deepgram_results(transcript, is_final=True, channels=channels)
                    )
                if mtype == "partial" and interim_results:
                    partial = (msg.get("text") or "").strip()
                    if partial:
                        await client_ws.send_json(
                            deepgram_results(partial, is_final=False, channels=channels)
                        )
            elif mtype == "stopped":
                await client_ws.send_json(
                    deepgram_metadata(request_id, models=models, channels=channels)
                )
                return
            elif mtype == "error":
                await client_ws.send_json({
                    "type": "Error",
                    "description": msg.get("message", "engine error"),
                })
            # keepalive and unknown types are ignored

    sender = asyncio.create_task(client_to_engine())
    receiver = asyncio.create_task(engine_to_client())
    try:
        await receiver
    finally:
        sender.cancel()
        try:
            await sender
        except asyncio.CancelledError:
            pass


async def _bridge_cloud(
    client_ws: WebSocket,
    session,
    loop,
    *,
    channels: int,
    interim_results: bool,
    pipeline,
    request_id: str,
    models: list[str],
) -> None:
    """Pump audio client->provider and translated transcripts provider->client.

    Final segments become ``is_final`` Deepgram ``Results``; interim hypotheses
    are forwarded only when the client requested ``interim_results``. A closing
    ``Metadata`` is always sent.
    """

    async def client_to_session():
        try:
            while True:
                message = await client_ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await session.send_audio(data)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                ctype = _control_type(text)
                if ctype in ("closestream", "finalize"):
                    break
                if ctype == "audio":  # Resona-native base64 JSON audio frame
                    raw = base64.b64decode(json.loads(text).get("data", "") or "")
                    if raw:
                        await session.send_audio(raw)
                # KeepAlive and unknown control frames are ignored.
        finally:
            try:
                await session.finish()
            except Exception:
                pass

    async def session_to_client():
        async for transcript in session:
            if transcript.is_final:
                text = await _postprocess(transcript.text, pipeline, loop)
                if text:
                    await client_ws.send_json(
                        deepgram_results(text, is_final=True, channels=channels)
                    )
            elif interim_results and transcript.text:
                await client_ws.send_json(
                    deepgram_results(transcript.text, is_final=False, channels=channels)
                )

    sender = asyncio.create_task(client_to_session())
    receiver = asyncio.create_task(session_to_client())

    done, _pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    if receiver not in done:
        # Client finished sending; drain trailing finals before closing upstream.
        try:
            await asyncio.wait_for(asyncio.shield(receiver), timeout=_CLOUD_FINAL_DRAIN_SECONDS)
        except asyncio.TimeoutError:
            pass

    await session.close()
    for task in (sender, receiver):
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # upstream close / parse errors after teardown
            log.debug("cloud bridge task ended: %s", exc)

    if client_ws.application_state == WebSocketState.CONNECTED:
        await client_ws.send_json(
            deepgram_metadata(request_id, models=models, channels=channels)
        )


def _control_type(text: str) -> str:
    """Return the lowercased ``type`` of a JSON control/text frame, or ''."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return ""
    return (payload.get("type") or "").lower()


async def _handle_control(text: str, engine_ws, sample_rate: int) -> bool:
    """Process a JSON control/text frame from the client.

    Returns False when the client signalled end-of-stream (CloseStream/Finalize).
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return True
    ctype = (payload.get("type") or "").lower()
    if ctype in ("closestream", "finalize"):
        return False
    if ctype == "audio":  # Resona-native JSON audio frame (base64 already)
        await engine_ws.send(json.dumps({
            "type": "audio",
            "data": payload.get("data", ""),
            "sample_rate": payload.get("sample_rate", sample_rate),
        }))
    # KeepAlive and others: ignore (engine has its own keepalive)
    return True


async def _postprocess(text: str, pipeline, loop) -> str:
    """Apply the optional profile pipeline to a final transcript chunk."""
    if pipeline is None:
        return text
    try:
        result = await loop.run_in_executor(None, lambda: pipeline.run(text))
        return result.text
    except Exception as exc:
        log.warning("profile postprocess failed, returning raw text: %s", exc)
        return text
