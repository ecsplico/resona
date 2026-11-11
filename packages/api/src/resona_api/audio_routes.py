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
from resona_job_core import Candidate, JobResult, PolicyDenied
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from . import engine_registry as reg
from . import clock, jobs_routes, storage
from .auth import Principal, client_principal
from .db.engine import engine as db_engine
from .db.models import Job, JobStatus, Organization, Worker
from .errors import api_error, validation_message
from .privacy import admit_speech, cloud_endpoint_allowed
from .settings_store import current_settings

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


def _org_engines(session: Session, organization_id: str) -> dict:
    """Engines this organization can actually use.

    Audio/document engines: candidate tuples of the organization's execution
    profiles that at least one approved, unrevoked worker of the organization is
    both hub-approved for and reported as tested (prepared). Cloud ASR candidates
    appear only when org settings enable cloud processing and approve their
    endpoint, and only on non-private workers. ``available`` means such a worker
    is online now. Synchronous TTS engines of ``/v1/audio/speech`` follow the
    same cloud rule. Nothing is probed over the network.
    """
    try:
        _, settings = current_settings(session, organization_id)
    except LookupError:
        return {"engines": [], "default": None}
    online = {w.worker_id for w in jobs_routes._online_workers(session, organization_id, clock.now())}
    workers = session.exec(
        select(Worker).where(
            Worker.organization_id == organization_id,
            Worker.approved.is_(True),
            Worker.revoked_at.is_(None),
        )
    ).all()
    offered: dict[Candidate, set[str]] = {}
    for worker in workers:
        approved = {Candidate.model_validate(c) for c in json.loads(worker.approved_capabilities or "[]")}
        tested = {Candidate.model_validate(c) for c in json.loads(worker.reported_capabilities or "[]")}
        for candidate in approved & tested:
            cloud = reg.cloud_asr_endpoint_id(candidate.engine) is not None
            if cloud and worker.approved_private:
                continue
            offered.setdefault(candidate, set()).add(worker.id)

    engines: dict[tuple[str, str], dict] = {}
    for profile_name, profile in sorted(settings.execution_profiles.items()):
        for kind in ("audio", "document"):
            for candidate in profile.candidates(kind):
                worker_ids = offered.get(candidate)
                if not worker_ids or not cloud_endpoint_allowed(settings, candidate.engine):
                    continue
                cloud = reg.cloud_asr_endpoint_id(candidate.engine) is not None
                entry = engines.setdefault((candidate.engine, kind), {
                    "name": candidate.engine,
                    "kind": "cloud" if cloud else "local",
                    "job_kind": kind,
                    "capabilities": ["stt"] if kind == "audio" else ["document"],
                    "private": not cloud,
                    "available": False,
                    "models": [],
                    "candidates": [],
                })
                entry["available"] = entry["available"] or bool(worker_ids & online)
                if candidate.model not in entry["models"]:
                    entry["models"].append(candidate.model)
                listed = next(
                    (c for c in entry["candidates"] if c["candidate"] == candidate.model_dump(mode="json")), None
                )
                if listed is None:
                    listed = {"candidate": candidate.model_dump(mode="json"), "execution_profiles": []}
                    entry["candidates"].append(listed)
                listed["execution_profiles"].append(profile_name)
    speech = [
        {
            "name": e.name,
            "kind": e.kind,
            "job_kind": "speech",
            "capabilities": ["tts"],
            "private": e.private,
            "available": e.available,
            "models": e.models,
            "candidates": [],
        }
        for e in reg.speech_catalogue()
        if e.kind == "local" or settings.cloud_enabled
    ]
    default_profile = settings.execution_profiles.get(settings.default_execution_profile)
    default = next(
        (
            c.engine
            for c in (default_profile.candidates("audio") if default_profile is not None else [])
            if (c.engine, "audio") in engines
        ),
        None,
    )
    return {"engines": [*engines.values(), *speech], "default": default}


@router.get("/v1/engines", tags=["Engines"])
def list_engines(principal: Principal = Depends(client_principal)):
    """Engines the caller's organization can use: tested, approved and policy-filtered."""
    with Session(db_engine) as session:
        return _org_engines(session, principal.organization_id)


def _form_bool(value: str | None, *, default: bool, name: str = "private") -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise api_error(422, "invalid_request", f"{name} must be true or false")


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
        raise api_error(422, "invalid_request", validation_message(exc)) from None


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
        raise api_error(410, "content_unavailable", "result content is unavailable") from None


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
        # ``speaker`` only on diarized results; other segments keep the pre-diarization shape.
        segments = [seg.model_dump(exclude_none=True) for seg in result.segments]
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
    ``private`` (default true), ``diarize`` (default false: label speakers, needs a
    worker that diarizes). Admission is identical to ``POST /v1/jobs``.
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
        diarize = _form_bool(fields.get("diarize"), default=False, name="diarize")
        options = jobs_routes.snapshot_options(jobs_routes.AudioOptions(diarize=diarize))
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
