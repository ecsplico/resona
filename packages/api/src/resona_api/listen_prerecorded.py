"""Deepgram-compatible prerecorded transcription: ``POST /v1/listen``.

An adapter onto the job path, like ``POST /v1/audio/transcriptions``: the raw
request body is the audio (any container the workers decode, or headerless
``linear16`` PCM with ``encoding=linear16``, which is wrapped into WAV before it
is stored). The route stores it as an upload, admits an audio job exactly like
``POST /v1/jobs`` and waits up to ``RESONA_SYNC_WAIT_SECONDS`` for a worker; on
timeout it answers 504 with the job ID and a ``Location`` header while the job
keeps running. The answer has Deepgram's prerecorded shape.
"""
import hashlib
import logging
import secrets
import struct
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from resona_job_core import JobResult
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from . import audio_routes, clock, jobs_routes, schemas, storage
from .auth import Principal, client_principal
from .db.engine import engine as db_engine
from .db.models import Upload
from .errors import api_error, validation_message
from .openapi import CLIENT

log = logging.getLogger(__name__)
router = APIRouter(tags=[CLIENT])

PCM_ENCODINGS = frozenset({"linear16", "pcm_s16le", "s16le"})
_WAV_HEADER_BYTES = 44
_SNIFF_BYTES = 16

_LISTEN_ERRORS = schemas.error_responses(schemas.merge_codes(schemas.UPLOAD, schemas.ADMISSION, {
    400: ["invalid_idempotency_key"],
    404: ["job_not_found"],
    409: ["job_cancelled"],
    410: ["content_unavailable"],
    422: [schemas.VALIDATION],
    502: ["job_failed"],
    504: ["timeout"],
}), model=schemas.JobErrorBody)
_LISTEN_ERRORS[502]["description"] = (
    "The job failed: `error` is the job's own error code (open set, e.g. `unsupported_input`), "
    "`job_failed` when it has none."
)
_LISTEN_ERRORS[504]["description"] = (
    "Error: `timeout`. The job keeps running; `job_id` and the `Location` header name it."
)


def wav_header(data_bytes: int, *, sample_rate: int, channels: int) -> bytes:
    """A 44-byte PCM WAV header for ``data_bytes`` of 16-bit little-endian samples."""
    block = channels * 2
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_bytes, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, sample_rate * block, block, 16,
        b"data", data_bytes,
    )


async def receive_raw_audio(
    request: Request, session: Session, organization_id: str, *,
    pcm: tuple[int, int] | None,
) -> str:
    """Stream the raw request body into org storage as one unattached audio upload.

    ``pcm`` = ``(sample_rate, channels)`` wraps headerless linear16 PCM into a WAV
    file; otherwise the body must start like an audio container. Returns the upload ID.
    """
    limit = jobs_routes.MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise api_error(413, "too_large", f"upload exceeds {limit} bytes")

    upload_id = "upl_" + secrets.token_hex(12)
    final = storage.upload_path(jobs_routes._storage_id(session, organization_id), upload_id)
    staged = storage.staging_path(final)
    size = 0
    head = b""
    try:
        with staged.open("w+b") as target:
            if pcm is not None:
                target.write(b"\x00" * _WAV_HEADER_BYTES)  # patched once the data size is known
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise api_error(413, "too_large", f"upload exceeds {limit} bytes")
                if len(head) < _SNIFF_BYTES:
                    head += chunk[: _SNIFF_BYTES - len(head)]
                target.write(chunk)
            if size == 0:
                raise api_error(422, "empty_upload", "the request body is empty")
            if pcm is not None:
                if size % (2 * pcm[1]):
                    raise api_error(422, "invalid_upload", "linear16 audio must hold whole 16-bit frames")
                target.seek(0)
                target.write(wav_header(size, sample_rate=pcm[0], channels=pcm[1]))
                media = ("audio", "audio/wav")
            else:
                media = jobs_routes.sniff_media(head)
                if media is None or media[0] != "audio":
                    raise api_error(
                        415, "unsupported_media",
                        "expected an audio container, or raw PCM with encoding=linear16",
                    )
            target.flush()
        digest = await run_in_threadpool(_sha256, staged)
        storage.promote(staged, final)
    except BaseException:
        storage.remove_file(staged)
        raise

    now = clock.now()
    stored_size = size + (_WAV_HEADER_BYTES if pcm is not None else 0)
    session.add(Upload(
        id=upload_id,
        organization_id=organization_id,
        sha256=digest,
        size=stored_size,
        media_family=media[0],
        media_type=media[1],
        created_at=now,
        expires_at=now + jobs_routes.UPLOAD_TTL,
    ))
    try:
        session.commit()
    except BaseException:
        storage.remove_file(final)
        raise
    log.info("Stored listen upload (%d bytes)", stored_size)
    return upload_id


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def model_engine(organization_id: str, model: Optional[str]) -> Optional[str]:
    """The engine a Deepgram ``model`` param selects on the job path: ``model`` itself when it
    names an audio engine the organization can use (``GET /v1/engines``), else None."""
    if not model:
        return None
    with Session(db_engine) as session:
        engines = audio_routes._org_engines(session, organization_id)["engines"]
    return model if any(e["name"] == model and e["job_kind"] == "audio" for e in engines) else None


def prerecorded_response(job_id: int, result: JobResult, *, channels: int) -> dict[str, Any]:
    """Deepgram's prerecorded response shape for a finished audio job."""
    asr = next((s for s in result.provenance.stages if s.stage == "asr"), None)
    utterances = []
    for segment in result.segments:
        utterance: dict[str, Any] = {
            "start": segment.start, "end": segment.end, "transcript": segment.text.strip(), "channel": 0,
        }
        if segment.speaker is not None:
            utterance["speaker"] = segment.speaker - 1  # Deepgram counts speakers from 0
        utterances.append(utterance)
    body: dict[str, Any] = {
        "metadata": {
            "request_id": str(job_id),
            "duration": result.segments[-1].end if result.segments else 0.0,
            "channels": channels,
            "model_info": {
                "engine": asr.engine if asr else None,
                "model": asr.model if asr else None,
            },
        },
        "results": {
            "channels": [{
                "detected_language": result.language,
                # Job results carry segment timestamps only, no per-word timing.
                "alternatives": [{"transcript": result.markdown, "confidence": None, "words": []}],
            }],
            "utterances": utterances,
        },
    }
    if result.structured:
        body["structured"] = result.structured
    return body


@router.post(
    "/v1/listen", tags=["Streaming"], operation_id="listen_transcribe",
    response_model=schemas.ListenPrerecordedResponse,
    responses=_LISTEN_ERRORS,
    openapi_extra={"requestBody": {
        "required": True,
        "description": "The audio: a container (WAV, MP3, Ogg, WebM, MP4, FLAC), or raw PCM "
                       "with `encoding=linear16`.",
        "content": {"audio/*": {"schema": {"type": "string", "format": "binary"}},
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }},
)
async def listen_transcribe(
    request: Request,
    principal: Principal = Depends(client_principal),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    language: Optional[str] = Query(default=None, description="Transcription language, e.g. `de`."),
    detect_language: bool = Query(
        default=False,
        description="Accepted for compatibility; jobs run in `language` or the organization's language.",
    ),
    model: Optional[str] = Query(
        default=None,
        description="Selects an engine only when it names one the organization can use "
                    "(`GET /v1/engines`); other values (`nova-3`) mean the default.",
    ),
    engine: Optional[str] = Query(default=None, description="Engine; wins over `model`."),
    profile: Optional[str] = Query(default=None, description="Stored postprocess profile name."),
    private: bool = Query(default=True, description="Private processing only (default)."),
    diarize: Optional[bool] = Query(
        default=None, description="Label speakers; unset = the execution profile's default.",
    ),
    encoding: Optional[str] = Query(default=None, description="`linear16` for raw PCM; omit for containers."),
    sample_rate: int = Query(default=16000, ge=8000, le=192000, description="Sample rate of raw PCM."),
    channels: int = Query(default=1, ge=1, le=8, description="Channels of raw PCM."),
    keyterm: list[str] = Query(
        default_factory=list,
        description="Accepted for compatibility; batch jobs take key terms from the profile's `initial_prompt`.",
    ),
):
    """Deepgram-compatible prerecorded transcription as a bounded wait on a queued audio job.

    Other Deepgram parameters (``smart_format``, ``punctuate``, ``utterances``,
    ``filler_words``, ``mip_opt_out``, ...) are accepted and ignored. Admission is
    identical to ``POST /v1/jobs``.
    """
    organization_id = principal.organization_id
    encoding = (encoding or "").strip().lower()
    if encoding and encoding not in PCM_ENCODINGS:
        raise api_error(
            422, "invalid_request",
            f"unsupported encoding '{encoding}': send linear16 PCM with encoding=linear16, "
            "or container audio without encoding",
        )
    pcm = (sample_rate, channels) if encoding else None
    engine_name = (engine or "").strip() or await run_in_threadpool(model_engine, organization_id, model)
    with Session(db_engine) as session:
        upload_id = await receive_raw_audio(request, session, organization_id, pcm=pcm)

    def build() -> tuple[jobs_routes.JobRequest, dict]:
        body: dict[str, Any] = {
            "kind": "audio",
            "upload_id": upload_id,
            "language": (language or "").strip() or None,
            "engine": engine_name,
            "profile": (profile or "").strip() or None,
            "private": private,
        }
        if diarize is not None:
            body["options"] = {"diarize": diarize}
        try:
            req = jobs_routes.JobRequest.model_validate(body)
        except ValidationError as exc:
            raise api_error(422, "invalid_request", validation_message(exc)) from None
        return req, jobs_routes.snapshot_options(jobs_routes.AudioOptions(diarize=bool(diarize)))

    job_id = await audio_routes.admit_sync_job(organization_id, upload_id, idempotency_key, build)
    outcome = await audio_routes.wait_sync_job(job_id)
    if isinstance(outcome, JSONResponse):
        return outcome
    return JSONResponse(prerecorded_response(job_id, outcome, channels=channels if pcm else 1))
