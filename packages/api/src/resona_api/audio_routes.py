"""OpenAI-compatible /v1/audio/* endpoints and /v1/engines discovery.

``POST /v1/audio/transcriptions`` is an adapter onto the job path: it stores the
upload, admits an audio job exactly like ``POST /v1/jobs`` (same settings
snapshot, candidates and policy checks) and waits up to
``RESONA_SYNC_WAIT_SECONDS`` for an approved worker to finish it. On timeout it
answers 504 with the job ID and a ``Location`` header; the job keeps running.
The hub never transcribes content itself.
"""
import asyncio
import json
import logging
import time
import uuid

from decouple import config
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from resona_job_core import JobResult, PolicyDenied
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from . import engine_registry as reg
from . import jobs_routes, storage
from .auth import Principal, client_principal
from .db.engine import engine as db_engine
from .db.models import Job, JobStatus, Organization
from .errors import api_error
from .privacy import admit_speech

log = logging.getLogger(__name__)
router = APIRouter()

SYNC_WAIT_SECONDS: float = config("RESONA_SYNC_WAIT_SECONDS", default=60.0, cast=float)
SYNC_POLL_SECONDS = 0.25
RESPONSE_FORMATS = frozenset({"json", "text", "verbose_json"})
# OpenAI SDKs always send a model; ``whisper-1`` means "the default", not an override.
_DEFAULT_MODEL_ALIASES = frozenset({"", "whisper-1"})
_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


def _http_error(exc: Exception) -> HTTPException:
    """Map a registry or provider error to an HTTPException."""
    if isinstance(exc, (reg.EngineNotFoundError, reg.CapabilityError,
                        reg.PrivacyViolationError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, reg.EngineUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, reg.NoEngineError):
        return HTTPException(status_code=409, detail=str(exc))
    name = type(exc).__name__
    if name == "MissingAPIKeyError":
        return HTTPException(status_code=503, detail=str(exc))
    if name == "ProviderHTTPError":
        return HTTPException(status_code=502, detail=str(exc))
    if name == "CloudTTSError":
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def _policy_denied(exc: PolicyDenied) -> JSONResponse:
    """A denied stage never reaches an engine or provider; report it as 403."""
    return JSONResponse(
        status_code=403,
        content={"error": "policy_denied", "message": f"{exc.stage}: {exc.reason}"},
    )


@router.get("/v1/engines", tags=["Engines"])
def list_engines(principal: Principal = Depends(client_principal)):
    """List every engine this gateway exposes, with capabilities and status."""
    catalogue = reg.get_catalogue(fresh=True)
    return {
        "engines": [
            {
                "name": e.name,
                "kind": e.kind,
                "capabilities": e.capabilities,
                "private": e.private,
                "available": e.available,
                "models": e.models,
            }
            for e in catalogue
        ],
        "default": reg.effective_default("stt", catalogue=catalogue),
    }


def _form_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise api_error(422, "invalid_request", "private must be true or false")


def _job_request(fields: dict[str, str], upload_id: str) -> tuple[jobs_routes.JobRequest, str]:
    """Map OpenAI form fields onto a canonical audio JobRequest; returns it with the response format.

    ``prompt`` and ``temperature`` are accepted for compatibility and ignored: jobs
    carry only typed options, never free-form engine arguments.
    """
    response_format = fields.get("response_format") or "json"
    if response_format not in RESPONSE_FORMATS:
        raise api_error(422, "invalid_request",
                        f"response_format must be one of {', '.join(sorted(RESPONSE_FORMATS))}")
    model = (fields.get("model") or "").strip()
    body = {
        "kind": "audio",
        "upload_id": upload_id,
        "execution_profile": fields.get("execution_profile") or None,
        "language": fields.get("language") or None,
        "engine": fields.get("engine") or None,
        "model": None if model in _DEFAULT_MODEL_ALIASES else model,
        "profile": fields.get("profile") or None,
        "private": _form_bool(fields.get("private"), default=True),
    }
    try:
        return jobs_routes.JobRequest.model_validate(body), response_format
    except ValidationError as exc:
        raise api_error(422, "invalid_request", jobs_routes._validation_message(exc)) from None


def _load_job(job_id: int) -> Job:
    with Session(db_engine) as session:
        return session.get(Job, job_id)


def _read_result(job: Job) -> JobResult:
    with Session(db_engine) as session:
        storage_id = session.get(Organization, job.organization_id).storage_id
    path = storage.result_path(storage_id, job.id)
    try:
        return JobResult.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        raise api_error(410, "result_expired", "result is no longer available") from None


def _job_error(job: Job) -> JSONResponse:
    code = job.error_code or "job_failed"
    message = job.error_message or code
    if job.status == JobStatus.CANCELLED:
        return JSONResponse({"error": "job_cancelled", "message": message, "job_id": job.id}, status_code=409)
    if code == "policy_denied":
        return JSONResponse({"error": code, "message": message, "job_id": job.id}, status_code=403)
    return JSONResponse({"error": code, "message": message, "job_id": job.id}, status_code=502)


def _openai_response(result: JobResult, response_format: str):
    text = result.markdown
    if response_format == "text":
        return PlainTextResponse(text)
    payload: dict = {"text": text}
    if response_format == "verbose_json":
        segments = [seg.model_dump() for seg in result.segments]
        payload.update({
            "language": result.language,
            "duration": segments[-1]["end"] if segments else 0.0,
            "segments": segments,
        })
    if result.structured:
        payload["structured"] = result.structured
    return JSONResponse(payload)


@router.post("/v1/audio/transcriptions", tags=["Audio"])
async def create_transcription(
    request: Request,
    principal: Principal = Depends(client_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """OpenAI-compatible speech-to-text as a bounded wait on a queued audio job.

    Multipart fields: ``file`` plus OpenAI ``model``, ``language``,
    ``response_format`` (json|text|verbose_json), ``prompt``/``temperature``
    (ignored) and Resona ``engine``, ``execution_profile``, ``profile``,
    ``private`` (default true). Admission is identical to ``POST /v1/jobs``.
    """
    organization_id = principal.organization_id
    with Session(db_engine) as session:
        stored, fields = await jobs_routes.receive_upload(request, session, organization_id)
    upload_id = stored["upload_id"]
    key = (idempotency_key or "").strip() or f"sync-{uuid.uuid4().hex}"
    try:
        if len(key) > jobs_routes.MAX_IDEMPOTENCY_KEY_CHARS or not key.isprintable():
            raise api_error(400, "invalid_idempotency_key", "Idempotency-Key is too long or not printable")
        req, response_format = _job_request(fields, upload_id)
        options = jobs_routes.AudioOptions().model_dump(mode="json")
        _, body = await run_in_threadpool(jobs_routes._admit, organization_id, key, req, options)
    except BaseException:
        await run_in_threadpool(jobs_routes.discard_upload, organization_id, upload_id)
        raise
    job_id = int(body["job_id"])

    deadline = time.monotonic() + SYNC_WAIT_SECONDS
    while True:
        job = await run_in_threadpool(_load_job, job_id)
        if job is None:
            raise api_error(404, "job_not_found", "job not found")
        if job.status in _TERMINAL:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return JSONResponse(
                {
                    "error": "timeout",
                    "message": f"job {job_id} did not finish within {SYNC_WAIT_SECONDS:g} s; it keeps running",
                    "job_id": job_id,
                },
                status_code=504,
                headers={"Location": f"/v1/jobs/{job_id}"},
            )
        await asyncio.sleep(min(SYNC_POLL_SECONDS, remaining))

    if job.status != JobStatus.COMPLETED:
        return _job_error(job)
    result = await run_in_threadpool(_read_result, job)
    return _openai_response(result, response_format)


class SpeechRequest(BaseModel):
    """Request body for POST /v1/audio/speech."""

    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    speed: float | None = None
    engine: str | None = None
    private: bool = True
    language: str | None = None


@router.post("/v1/audio/speech", tags=["Audio"])
def create_speech(
    body: SpeechRequest,
    principal: Principal = Depends(client_principal),
):
    """OpenAI-compatible synchronous text-to-speech (local or cloud engines)."""
    try:
        with Session(db_engine) as session:
            info = admit_speech(
                session,
                principal.organization_id,
                private=body.private,
                engine=body.engine,
            )
    except PolicyDenied as exc:
        return _policy_denied(exc)
    except reg.EngineError as exc:
        raise _http_error(exc)
    try:
        result = reg.run_tts(
            info,
            body.input,
            model=body.model,
            voice=body.voice,
            response_format=body.response_format,
            speed=body.speed,
            language=body.language,
        )
    except Exception as exc:
        raise _http_error(exc)
    return StreamingResponse(
        iter([result["audio"]]), media_type=result["content_type"]
    )
