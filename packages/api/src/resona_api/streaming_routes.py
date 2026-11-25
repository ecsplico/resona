"""Deepgram-compatible live streaming transcription: ``WS /v1/listen``.

This endpoint mirrors Deepgram's live streaming API closely enough that the
official Deepgram SDK works against Resona unchanged for the core path:

  * Connect with query params (``model``, ``language``, ``encoding``,
    ``sample_rate``, ``channels``, ``interim_results``, ``detect_language``,
    ``keyterm``).
  * Authenticate with ``Authorization: Token <token>`` (also accepts
    ``Bearer``, ``X-API-Key``, or ``?token=`` / ``?api_key=``); the credential
    resolves to an organization and nothing else in the request selects it.
  * Stream raw ``linear16`` PCM (``encoding=linear16``) or, with no
    ``encoding``, container audio (WebM/Opus, Ogg, WAV, MP4, FLAC, ID3-tagged
    MP3) as binary WebSocket frames; container audio is decoded through an
    ``ffmpeg`` subprocess per socket.
  * Receive ``Results`` (interim + final, finals with ``speech_final: true``)
    and a closing ``Metadata`` message that names the engine used.

Parameter rules: ``engine`` wins; ``model`` selects an engine only when it names
one this organization can stream (``GET /v1/listen/engines``), otherwise it is
ignored (``model=nova-3`` from a Deepgram SDK means "the default").
``detect_language=true`` lets the engine detect the language. Each ``keyterm``
(repeatable) biases recognition: local engine-servers get the key terms and the
postprocess profile's ``initial_prompt`` as their ``initial_prompt``; cloud
bridges pass them to the provider as ``keyterms``. Other Deepgram params
(``smart_format``, ``endpointing``, ``vad_events``, ``mip_opt_out``,
``punctuate``, ``utterances``, ``diarize``, ``filler_words``) are accepted and
ignored.

Resona extensions: ``engine`` (pick a specific engine), ``profile`` (apply a
postprocessing profile to each final transcript) and ``private`` (default true:
cloud bridges are refused).

The endpoint bridges the Deepgram wire protocol to one of two upstreams,
resolved via the gateway catalogue:

  * **local** engine-server — proxied to its ``/ws/live`` WebSocket.
  * **cloud** provider with a realtime API (Deepgram, ElevenLabs) — proxied to
    the provider's live STT WebSocket via ``resona_cloud_stt.streaming``.

Cloud providers without a streaming API (OpenAI) are rejected.

``GET /v1/listen/engines`` lists the engines this organization can stream
through ``WS /v1/listen`` right now, plus the default one.
"""
import asyncio
import base64
import contextlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import websockets
from fastapi import APIRouter, Depends, WebSocket
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from resona_cloud_stt.streaming import open_stream, supports_streaming
from resona_job_core import OrganizationSettings, PolicyDenied

from . import engine_registry as reg
from . import schemas
from .auth import Principal, client_principal, websocket_principal
from .db.engine import engine as db_engine
from .deps import get_db_session
from .openapi import CLIENT
from .privacy import admit, cloud_endpoint_allowed
from .settings_store import current_settings

# How long to keep draining final transcripts after the client stops sending,
# for providers (e.g. ElevenLabs) that do not close the socket on commit.
_CLOUD_FINAL_DRAIN_SECONDS = 3.0

log = logging.getLogger(__name__)
router = APIRouter(tags=[CLIENT])


# Encodings that map directly to the engine's int16 PCM input. No ``encoding`` means
# "sniff": raw PCM passes through, container audio is decoded with ffmpeg.
_PCM_ENCODINGS = {"linear16", "pcm_s16le", "s16le"}
_END_OF_STREAM = ("closestream", "finalize")

# Container audio is decoded by this executable (looked up on PATH per socket).
FFMPEG = "ffmpeg"
_PCM_CHUNK_BYTES = 8192
# Frames buffered between the client reader and the bridge (backpressure on ffmpeg).
_QUEUE_FRAMES = 64
# Key terms: at most this many from the query; the local prompt is cut at this length.
MAX_KEYTERMS = 100
_MAX_PROMPT_CHARS = 800


def _streamable(info) -> bool:
    """Whether the bridge can stream this engine: a local engine-server with a
    URL, or a cloud provider with a realtime API."""
    return (info.kind == "local" and bool(info.url)) or (
        info.kind == "cloud" and supports_streaming(info.provider or "")
    )


def _org_listen_engines(settings: OrganizationSettings) -> list[reg.EngineInfo]:
    """Engines of the catalogue this organization can stream right now."""
    return [
        e for e in reg.get_catalogue()
        if "stt" in e.capabilities and e.available and _streamable(e) and (
            e.kind == "local"
            or (settings.cloud_enabled and cloud_endpoint_allowed(settings, e.name))
        )
    ]


@router.get(
    "/v1/listen/engines", tags=["Streaming"], operation_id="listen_engines_list",
    response_model=schemas.ListenEngineList, responses=schemas.error_responses(),
)
def list_listen_engines(
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict:
    """Engines this organization can stream live via ``WS /v1/listen`` right now.

    Mirrors the bridge dispatch below: an available local engine-server, or a
    cloud provider with a realtime API when the org enables cloud processing and
    approves its endpoint. ``default`` is the organization's ``live.engine`` if it is
    listed (a cloud one too: clients open it with ``private=false``), else
    ``RESONA_DEFAULT_ENGINE`` if it is a listed private engine, else the first
    listed local engine.
    """
    try:
        _, settings = current_settings(session, principal.organization_id)
    except LookupError:
        return {"engines": [], "default": None}
    engines = [
        {"name": e.name, "kind": e.kind, "private": e.private, "models": e.models}
        for e in _org_listen_engines(settings)
    ]
    live_engine = settings.live.engine if settings.live else None
    configured = reg.default_engine_name()
    if live_engine and any(e["name"] == live_engine for e in engines):
        default = live_engine
    elif any(e["name"] == configured and e["private"] for e in engines):
        default = configured
    else:
        default = next((e["name"] for e in engines if e["kind"] == "local"), None)
    return {"engines": engines, "default": default}


def model_engine(session: Session, organization_id: str, model: str | None) -> str | None:
    """The engine a Deepgram ``model`` param selects: ``model`` itself when it names an
    engine the organization can stream, else None (the organization default applies)."""
    if not model:
        return None
    try:
        _, settings = current_settings(session, organization_id)
    except LookupError:
        return None
    return model if any(e.name == model for e in _org_listen_engines(settings)) else None


def _truthy(value: str | None, default: bool = False) -> bool:
    """Parse a Deepgram-style boolean query param."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def merge_keyterms(keyterms: list[str], profile_terms: list[str]) -> list[str]:
    """Query key terms (first :data:`MAX_KEYTERMS`) then the profile's ``initial_prompt``
    terms, stripped, without empty entries and case-insensitive duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for term in [*keyterms[:MAX_KEYTERMS], *profile_terms]:
        term = term.strip()
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            merged.append(term)
    return merged


def keyterm_prompt(terms: list[str]) -> str | None:
    """Key terms as a Whisper ``initial_prompt`` (comma-separated, cut at a term boundary)."""
    prompt = ""
    for term in terms:
        candidate = f"{prompt}, {term}" if prompt else term
        if len(candidate) > _MAX_PROMPT_CHARS:
            break
        prompt = candidate
    return prompt or None


def _resolve_principal(websocket: WebSocket) -> Principal | None:
    """Authenticate the handshake against the credential store; None when rejected."""
    with Session(db_engine) as session:
        return websocket_principal(websocket, session)


async def _close(websocket: WebSocket, code: int = 1000) -> None:
    """Close an accepted client socket unless either side already closed it.

    The client may vanish between the state check and the send; uvicorn then
    rejects the close (``RuntimeError``) or the send fails (``WebSocketDisconnect``).
    Both mean the session is already over, so neither is an error.
    """
    if (websocket.client_state != WebSocketState.CONNECTED
            or websocket.application_state != WebSocketState.CONNECTED):
        return
    try:
        await websocket.close(code=code)
    except (RuntimeError, WebSocketDisconnect) as exc:
        log.debug("client socket already closed: %s", exc)


async def _send_error(websocket: WebSocket, description: str) -> None:
    """Send a Deepgram ``Error`` frame if the client socket is still open."""
    if websocket.application_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json({"type": "Error", "description": description})
    except (RuntimeError, WebSocketDisconnect) as exc:
        log.debug("client socket already closed: %s", exc)


def _ws_url(http_url: str, language: str | None, initial_prompt: str | None = None) -> str:
    """Convert an engine-server http(s) base URL to its /ws/live WebSocket URL.

    ``language`` None asks the engine to detect it (``language=auto``).
    """
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    params = {"language": language or "auto"}
    if initial_prompt:
        params["initial_prompt"] = initial_prompt
    return f"{base}/ws/live?{urlencode(params)}"


def deepgram_results(
    transcript: str, *, is_final: bool, channels: int = 1
) -> dict:
    """Build a Deepgram-shaped ``Results`` message. Finals are ``speech_final``."""
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


def deepgram_metadata(
    request_id: str, *, models: list[str], channels: int = 1, engine: str | None = None,
) -> dict:
    """Build a Deepgram-shaped ``Metadata`` message (sent at end of stream).

    ``engine`` (the engine the session ran on) adds Resona's ``engine`` field and a
    Deepgram ``model_info`` entry per model.
    """
    message = {
        "type": "Metadata",
        "transaction_key": "deprecated",
        "request_id": request_id,
        "sha256": "",
        "created": datetime.now(timezone.utc).isoformat(),
        "duration": 0.0,
        "channels": channels,
        "models": models,
    }
    if engine is not None:
        message["engine"] = engine
        message["model_info"] = {m: {"name": engine, "version": "", "arch": m} for m in models}
    return message


# ── Client audio: raw PCM or container audio through ffmpeg ──────────────────

class StreamAborted(Exception):
    """The client stream cannot go on; the bridge sends ``message`` as an Error frame
    and closes the socket with ``code``."""

    def __init__(self, message: str, code: int = 1011) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def is_container_audio(head: bytes) -> bool:
    """Whether a first binary frame starts like a container format ffmpeg decodes.

    MP3 counts only with an ID3 tag: a bare MPEG frame sync (``FF Ex``) is also a
    common pair of raw PCM bytes.
    """
    return (
        head.startswith(b"\x1a\x45\xdf\xa3")       # WebM / Matroska (EBML)
        or head.startswith(b"OggS")                # Ogg (Opus, Vorbis)
        or (head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or head[4:8] == b"ftyp"                    # MP4 / M4A
        or head.startswith(b"fLaC")
        or head.startswith(b"ID3")                 # MP3 with an ID3 tag
    )


def ffmpeg_args(sample_rate: int) -> list[str]:
    """ffmpeg arguments: any container on stdin to mono linear16 at ``sample_rate`` on stdout."""
    return [
        "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1",
    ]


class ClientAudio:
    """Audio and control frames of the client socket, audio as linear16 PCM.

    Binary frames pass through unchanged, unless ``sniff`` (no ``encoding`` param)
    and the first binary frame starts like a container (:func:`is_container_audio`):
    then every binary frame goes into an ``ffmpeg`` subprocess and its PCM output
    (mono, ``sample_rate``) comes out instead. :meth:`frames` yields ``("audio",
    pcm)`` and ``("text", frame)``; an end-of-stream text frame (CloseStream,
    Finalize) comes only after ffmpeg has drained. Iteration ends at client
    disconnect and raises :class:`StreamAborted` when decoding cannot go on.
    ffmpeg is killed when iteration stops (use ``contextlib.aclosing``).
    """

    def __init__(self, ws: WebSocket, *, sniff: bool, sample_rate: int) -> None:
        self._ws = ws
        self._sniff = sniff
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_FRAMES)
        self._decided = not sniff
        self._proc: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None

    @property
    def decoding(self) -> bool:
        """Whether container audio is being decoded through ffmpeg."""
        return self._proc is not None

    async def frames(self):
        pump = asyncio.create_task(self._pump())
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                if isinstance(item, StreamAborted):
                    raise item
                yield item
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await self._kill()

    async def _pump(self) -> None:
        end: StreamAborted | None = None
        try:
            while True:
                message = await self._ws.receive()
                if message["type"] == "websocket.disconnect":
                    await self._kill()
                    break
                data = message.get("bytes")
                if data is not None:
                    if data:
                        await self._audio(data)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                if self._proc is not None and _control_type(text) in _END_OF_STREAM:
                    await self._drain()
                await self._queue.put(("text", text))
        except StreamAborted as exc:
            end = exc
        except (RuntimeError, WebSocketDisconnect) as exc:  # client socket already gone
            log.debug("client audio reader ended: %s", exc)
        await self._queue.put(end)

    async def _audio(self, data: bytes) -> None:
        if not self._decided:
            self._decided = True
            if is_container_audio(data):
                await self._start()
        if self._proc is None:
            await self._queue.put(("audio", data))
            return
        if self._proc.stdin.is_closing():
            return  # audio after end-of-stream: ffmpeg already drained
        try:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise StreamAborted(f"could not decode the audio: {await self._stderr()}") from None

    async def _start(self) -> None:
        path = shutil.which(FFMPEG)
        if path is None:
            raise StreamAborted(
                "container audio needs ffmpeg on the hub, which is not installed; "
                "send raw PCM with encoding=linear16"
            )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                path, *ffmpeg_args(self._sample_rate),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise StreamAborted(f"could not start ffmpeg: {exc}") from None
        self._stdout_task = asyncio.create_task(self._read_pcm(self._proc))

    async def _read_pcm(self, proc: asyncio.subprocess.Process) -> None:
        """Forward ffmpeg's PCM in whole 16-bit samples (an odd byte waits for the next read)."""
        carry = b""
        while True:
            chunk = await proc.stdout.read(_PCM_CHUNK_BYTES)
            if not chunk:
                break
            chunk = carry + chunk
            cut = len(chunk) - len(chunk) % 2
            carry = chunk[cut:]
            if cut:
                await self._queue.put(("audio", chunk[:cut]))

    async def _drain(self) -> None:
        """Close ffmpeg's stdin and wait until all its PCM is queued."""
        proc = self._proc
        if not proc.stdin.is_closing():
            proc.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await proc.stdin.wait_closed()
        await self._stdout_task
        code = await proc.wait()
        if code != 0:
            log.warning("ffmpeg exited with %s while decoding live audio: %s", code, await self._stderr())

    async def _stderr(self) -> str:
        if self._proc is None or self._proc.stderr is None:
            return "ffmpeg failed"
        with contextlib.suppress(Exception):
            if self._proc.returncode is None:
                await asyncio.wait_for(self._proc.wait(), timeout=1.0)
            text = (await self._proc.stderr.read()).decode("utf-8", "replace").strip()
            if text:
                return text[-300:]
        return "ffmpeg failed"

    async def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        task = self._stdout_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@router.websocket("/v1/listen")
async def listen(websocket: WebSocket):
    """Deepgram-compatible live transcription WebSocket."""
    principal = _resolve_principal(websocket)
    if principal is None:
        await websocket.close(code=1008)  # policy violation
        return

    qp = websocket.query_params
    detect_language = _truthy(qp.get("detect_language"))
    language = None if detect_language else (qp.get("language") or None)
    encoding = (qp.get("encoding") or "").lower()
    sample_rate = int(qp.get("sample_rate") or 16000)
    channels = int(qp.get("channels") or 1)
    interim_results = _truthy(qp.get("interim_results"), default=False)
    engine_name = qp.get("engine") or None
    model = qp.get("model") or None
    profile_name = qp.get("profile") or None
    private = _truthy(qp.get("private"), default=True)
    keyterms = qp.getlist("keyterm")

    await websocket.accept()

    if encoding and encoding not in _PCM_ENCODINGS:
        await websocket.send_json({
            "type": "Error",
            "description": f"unsupported encoding '{encoding}': send linear16 PCM with "
                           "encoding=linear16, or container audio without encoding",
        })
        await _close(websocket, code=1003)
        return

    # Admit the whole chain (settings, engine, postprocess profile) before any audio flows.
    try:
        with Session(db_engine) as session:
            if engine_name is None:
                engine_name = model_engine(session, principal.organization_id, model)
            admission = admit(
                session,
                principal.organization_id,
                private=private,
                engine=engine_name,
                profile=profile_name,
                language=language,
                live=True,
            )
    except PolicyDenied as exc:
        await websocket.send_json({
            "type": "Error",
            "description": f"policy_denied: {exc.stage}: {exc.reason}",
        })
        await _close(websocket, code=1008)
        return
    except (reg.EngineError, LookupError, ValueError) as exc:
        await websocket.send_json({"type": "Error", "description": str(exc)})
        await _close(websocket, code=1011)
        return

    info = admission.engine
    language = None if detect_language else admission.language
    terms = merge_keyterms(keyterms, admission.profile.initial_prompt)
    loop = asyncio.get_event_loop()
    common = dict(
        sample_rate=sample_rate, channels=channels, interim_results=interim_results,
        pipeline=admission.pipeline, request_id=str(uuid.uuid4()), models=info.models,
        decode=not encoding,
    )

    if _streamable(info) and info.kind == "local":
        await _run_local_bridge(
            websocket, info, language=language, initial_prompt=keyterm_prompt(terms), **common,
        )
    elif _streamable(info):
        await _run_cloud_bridge(
            websocket, info, language=language, loop=loop, keyterms=terms, **common,
        )
    else:
        await websocket.send_json({
            "type": "Error",
            "description": f"engine '{info.name}' does not support live streaming "
                           "(no realtime API)",
        })
        await _close(websocket, code=1003)


async def _run_local_bridge(websocket, info, *, language, sample_rate, channels,
                            interim_results, pipeline, request_id, models,
                            decode=False, initial_prompt=None):
    """Proxy the client to a local engine-server ``/ws/live``."""
    loop = asyncio.get_event_loop()
    audio = ClientAudio(websocket, sniff=decode, sample_rate=sample_rate)
    code = 1000
    try:
        url = _ws_url(info.url, language, initial_prompt)
        async with websockets.connect(url, max_size=None) as engine_ws:
            await _bridge(
                websocket, engine_ws, loop,
                audio=audio, sample_rate=sample_rate, channels=channels,
                interim_results=interim_results, pipeline=pipeline,
                request_id=request_id, models=models, engine=info.name,
            )
    except StreamAborted as exc:
        await _send_error(websocket, exc.message)
        code = exc.code
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception as exc:  # engine unreachable, etc.
        log.error("streaming bridge failed: %s", exc, exc_info=True)
        await _send_error(websocket, str(exc))
    finally:
        await _close(websocket, code=code)


async def _run_cloud_bridge(websocket, info, *, language, loop, sample_rate, channels,
                            interim_results, pipeline, request_id, models,
                            decode=False, keyterms=()):
    """Proxy the client to a cloud provider's realtime STT WebSocket."""
    try:
        api_key = reg.cloud_api_key(info.provider)
    except reg.EngineError as exc:
        await websocket.send_json({"type": "Error", "description": str(exc)})
        await _close(websocket, code=1011)
        return

    model = info.models[0] if info.models else None
    try:
        session = await open_stream(
            info.provider, api_key=api_key, model=model, language=language,
            sample_rate=sample_rate, interim_results=interim_results,
            keyterms=tuple(keyterms),
        )
    except Exception as exc:
        log.error("cloud stream open failed (%s): %s", info.provider, exc, exc_info=True)
        await _send_error(websocket, f"failed to open {info.provider} stream: {exc}")
        await _close(websocket, code=1011)
        return

    code = 1000
    try:
        await _bridge_cloud(
            websocket, session, loop,
            audio=ClientAudio(websocket, sniff=decode, sample_rate=sample_rate),
            channels=channels, interim_results=interim_results,
            pipeline=pipeline, request_id=request_id, models=models, engine=info.name,
        )
    except StreamAborted as exc:
        await _send_error(websocket, exc.message)
        code = exc.code
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception as exc:
        log.error("cloud streaming bridge failed: %s", exc, exc_info=True)
    finally:
        await session.close()
        await _close(websocket, code=code)


def _aborted(task: asyncio.Task) -> StreamAborted | None:
    """The :class:`StreamAborted` a finished task raised, if any."""
    if task.done() and not task.cancelled() and isinstance(task.exception(), StreamAborted):
        return task.exception()
    return None


async def _bridge(
    client_ws: WebSocket,
    engine_ws,
    loop,
    *,
    audio: ClientAudio,
    sample_rate: int,
    channels: int,
    interim_results: bool,
    pipeline,
    request_id: str,
    models: list[str],
    engine: str | None = None,
) -> None:
    """Pump audio client->engine and translated transcripts engine->client.

    Raises :class:`StreamAborted` when the client audio cannot be decoded.
    """

    async def client_to_engine():
        try:
            async with contextlib.aclosing(audio.frames()) as frames:
                async for kind, data in frames:
                    if kind == "audio":
                        await engine_ws.send(json.dumps({
                            "type": "audio",
                            "data": base64.b64encode(data).decode("ascii"),
                            "sample_rate": sample_rate,
                        }))
                    elif not await _handle_control(data, engine_ws, sample_rate):
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
                    deepgram_metadata(request_id, models=models, channels=channels, engine=engine)
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
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        aborted = _aborted(sender)
        if aborted is not None:
            raise aborted
        await receiver
    finally:
        for task in (receiver, sender):
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StreamAborted):
                pass


async def _bridge_cloud(
    client_ws: WebSocket,
    session,
    loop,
    *,
    audio: ClientAudio,
    channels: int,
    interim_results: bool,
    pipeline,
    request_id: str,
    models: list[str],
    engine: str | None = None,
) -> None:
    """Pump audio client->provider and translated transcripts provider->client.

    Final segments become ``is_final`` Deepgram ``Results``; interim hypotheses
    are forwarded only when the client requested ``interim_results``. A closing
    ``Metadata`` is always sent, unless the client audio could not be decoded
    (:class:`StreamAborted` is raised then).
    """

    async def client_to_session():
        try:
            async with contextlib.aclosing(audio.frames()) as frames:
                async for kind, data in frames:
                    if kind == "audio":
                        await session.send_audio(data)
                        continue
                    ctype = _control_type(data)
                    if ctype in _END_OF_STREAM:
                        break
                    if ctype == "audio":  # Resona-native base64 JSON audio frame
                        raw = base64.b64decode(json.loads(data).get("data", "") or "")
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
    aborted = _aborted(sender)
    if receiver not in done and aborted is None:
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

    if aborted is not None:
        raise aborted
    if client_ws.application_state == WebSocketState.CONNECTED:
        await client_ws.send_json(
            deepgram_metadata(request_id, models=models, channels=channels, engine=engine)
        )


def _control_type(text: str) -> str:
    """Return the lowercased ``type`` of a JSON control/text frame, or ''."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
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
    if not isinstance(payload, dict):
        return True
    ctype = (payload.get("type") or "").lower()
    if ctype in _END_OF_STREAM:
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
