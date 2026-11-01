"""Client job routes: uploads, durable job submission, status, results, cancel, delete.

Organization always comes from the credential; other organizations' IDs are 404.
Inputs are streamed into generated file names under ``storage``; the hub never
processes content itself. Jobs wait for an approved pull worker
(``worker_routes``), and every admission resolves the organization's current
settings revision into an immutable ``execution_snapshot`` that is
policy-checked before the job exists.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from decouple import config
from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from resona_job_core import (
    Candidate,
    Endpoint,
    EndpointKind,
    OrganizationSettings,
    PolicyDenied,
    Stage,
    check_policy,
)
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import ProfileError

from . import clock, releases, storage
from . import engine_registry as reg
from .auth import ROLE_ORG_ADMIN, Principal, client_principal
from .broker import _finish_terminal, _immediate, _remove_input
from .db.engine import engine
from .db.models import IdempotencyRecord, Job, JobStatus, Organization, ProfileRevision, Upload, Worker
from .deps import get_db_session
from .errors import api_error
from .privacy import ProfileNotFound, is_profile_name, load_org_profile
from .settings_store import current_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Job"])

MAX_UPLOAD_BYTES: int = config("RESONA_MAX_UPLOAD_BYTES", default=200 * 1024 * 1024, cast=int)
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_JOB_REQUEST_BYTES = 64 * 1024
UPLOAD_TTL = timedelta(hours=1)
JOB_DEADLINE = timedelta(hours=24)
WORKER_ONLINE_WINDOW = timedelta(seconds=90)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_IDEMPOTENCY_KEY_CHARS = 200
MAX_FORM_FIELDS = 32
MAX_FORM_FIELD_BYTES = 4096
_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
_SNIFF_BYTES = 16


# ── Request models ────────────────────────────────────────────────────────

class AudioOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translate: bool = False
    word_timestamps: bool = False
    vad_filter: bool = True


class DocumentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: Optional[list[Literal["de", "en"]]] = Field(default=None, min_length=1)
    ocr: Optional[Literal["auto", "force", "off"]] = None
    ocr_engine: Optional[Literal["ocrmac", "rapidocr", "tesseract"]] = None
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    pipeline: Optional[Literal["standard"]] = None

    @model_validator(mode="after")
    def _check_page_range(self) -> "DocumentOptions":
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must be <= page_end")
        return self


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = Field(default=None, max_length=200)
    session_id: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default=None, max_length=200)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["audio", "document"]
    upload_id: str = Field(max_length=64)
    execution_profile: Optional[str] = Field(default=None, max_length=64)
    language: Optional[str] = Field(default=None, pattern=r"^[a-z]{2,3}$")
    engine: Optional[str] = Field(default=None, max_length=128)
    model: Optional[str] = Field(default=None, max_length=256)
    profile: Optional[str] = Field(default=None, max_length=64)
    options: Optional[dict[str, Any]] = None
    private: bool = True
    provenance: Optional[Provenance] = None
    purpose: Literal["production", "evaluation"] = "production"
    # Evaluation jobs name the staged release under test; workers on exactly that release run them.
    release_id: Optional[str] = Field(default=None, pattern=r"^[0-9][0-9A-Za-z.+-]{0,63}$")


_OPTIONS_MODEL: dict[str, type[BaseModel]] = {"audio": AudioOptions, "document": DocumentOptions}


# ── Helpers ───────────────────────────────────────────────────────────────

def _storage_id(session: Session, organization_id: str) -> str:
    return session.get(Organization, organization_id).storage_id


def _owned_job(session: Session, organization_id: str, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.organization_id != organization_id:
        raise api_error(404, "job_not_found", "job not found")
    return job


def _iso(value: datetime | None) -> str | None:
    return None if value is None else clock.as_utc(value).isoformat()


def _snapshot_candidates(job: Job) -> set[Candidate]:
    raw = json.loads(job.execution_snapshot or "{}").get("candidates", [])
    return {Candidate.model_validate(item) for item in raw}


def _online_workers(session: Session, organization_id: str, now: datetime) -> list[tuple[bool, set[Candidate]]]:
    """``(approved_private, approved capabilities)`` of approved workers seen recently."""
    rows = session.exec(
        select(Worker).where(Worker.organization_id == organization_id, Worker.revoked_at.is_(None))
    ).all()
    online = []
    for worker in rows:
        if not worker.approved or worker.last_seen is None:
            continue
        if clock.as_utc(worker.last_seen) < now - WORKER_ONLINE_WINDOW:
            continue
        caps = {Candidate.model_validate(item) for item in json.loads(worker.approved_capabilities or "[]")}
        online.append((worker.approved_private, caps))
    return online


def _queue_reason(job: Job, workers: list[tuple[bool, set[Candidate]]]) -> str | None:
    if job.status != JobStatus.PENDING:
        return None
    wanted = _snapshot_candidates(job)
    capable = any((private or not job.private) and caps & wanted for private, caps in workers)
    return "waiting_capacity" if capable else "no_capable_worker"


CONTENT_UNAVAILABLE = "content_unavailable"
"""Error code of a job whose payload files did not survive a restore (``resona-admin restore``)."""


def _result_available(job: Job, now: datetime) -> bool:
    if job.status != JobStatus.COMPLETED or not job.result_manifest or job.error_code == CONTENT_UNAVAILABLE:
        return False
    return job.result_expires_at is None or clock.as_utc(job.result_expires_at) > now


def _view(job: Job, workers: list[tuple[bool, set[Candidate]]], now: datetime) -> dict[str, Any]:
    error = None
    if job.error_code and (job.status != JobStatus.COMPLETED or job.error_code == CONTENT_UNAVAILABLE):
        error = {"code": job.error_code, "message": job.error_message or job.error_code}
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status.value,
        "queue_reason": _queue_reason(job, workers),
        "private": job.private,
        "execution_profile": job.execution_profile,
        "candidate": json.loads(job.candidate) if job.candidate else None,
        "attempt": job.attempt,
        "error": error,
        "result_available": _result_available(job, now),
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "deadline_at": _iso(job.deadline_at),
    }


def _single_view(session: Session, job: Job) -> dict[str, Any]:
    now = clock.now()
    return _view(job, _online_workers(session, job.organization_id, now), now)


# ── Uploads ───────────────────────────────────────────────────────────────

def sniff_media(head: bytes) -> tuple[str, str] | None:
    """``(media_family, media_type)`` from leading magic bytes, or None if unsupported."""
    if head.startswith(b"%PDF-"):
        return "document", "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "document", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "document", "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "document", "image/tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio", "audio/mpeg"
    if head.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio", "audio/flac"
    if head[4:8] == b"ftyp":
        return "audio", "audio/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio", "audio/webm"
    return None


class _TooLarge(Exception):
    pass


class _BadForm(Exception):
    pass


class _FileField:
    """Multipart callbacks that stream the single ``file`` part into ``target``, hashing it.

    Other parts are small text form fields, collected into ``fields`` (bounded count and size).
    """

    def __init__(self, target, limit: int) -> None:
        self._target = target
        self._limit = limit
        self._field = b""
        self._value = b""
        self._name: bytes | None = None
        self._active = False
        self._form: bytearray | None = None
        self.fields: dict[str, str] = {}
        self.seen = 0
        self.size = 0
        self.head = b""
        self.digest = hashlib.sha256()

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
        }

    def _part_begin(self) -> None:
        self._name = None
        self._field = self._value = b""

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._field += data[start:end]

    def _header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        if self._field.strip().lower() == b"content-disposition":
            _, params = parse_options_header(self._value)
            self._name = params.get(b"name")
        self._field = self._value = b""

    def _headers_finished(self) -> None:
        self._active = self._name == b"file"
        self._form = None
        if self._active:
            self.seen += 1
        elif self._name is not None:
            if len(self.fields) >= MAX_FORM_FIELDS:
                raise _BadForm
            self._form = bytearray()

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if self._form is not None:
            self._form += data[start:end]
            if len(self._form) > MAX_FORM_FIELD_BYTES:
                raise _BadForm
            return
        if not self._active or self.seen > 1:
            return
        chunk = data[start:end]
        self.size += len(chunk)
        if self.size > self._limit:
            raise _TooLarge
        if len(self.head) < _SNIFF_BYTES:
            self.head += chunk[: _SNIFF_BYTES - len(self.head)]
        self.digest.update(chunk)
        self._target.write(chunk)

    def _part_end(self) -> None:
        if self._form is not None and self._name is not None:
            try:
                self.fields[self._name.decode()] = self._form.decode()
            except UnicodeDecodeError:
                raise _BadForm from None
        self._form = None
        self._active = False


def _too_large() -> Exception:
    return api_error(413, "too_large", f"upload exceeds {MAX_UPLOAD_BYTES} bytes")


async def receive_upload(
    request: Request, session: Session, organization_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Stream the multipart ``file`` part into org storage as one unattached upload.

    Returns ``({upload_id, sha256, size}, form_fields)``; the other multipart parts
    are returned as bounded text fields for routes that take form parameters.
    """
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise api_error(422, "invalid_upload", "expected multipart/form-data with a 'file' field")
    limit = MAX_UPLOAD_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit + MULTIPART_OVERHEAD_BYTES:
        raise _too_large()

    upload_id = "upl_" + secrets.token_hex(12)
    final = storage.upload_path(_storage_id(session, organization_id), upload_id)
    staged = storage.staging_path(final)
    try:
        with staged.open("wb") as target:
            field = _FileField(target, limit)
            parser = MultipartParser(boundary, field.callbacks())
            try:
                async for chunk in request.stream():
                    parser.write(chunk)
                parser.finalize()
            except _TooLarge:
                raise _too_large() from None
            except _BadForm:
                raise api_error(422, "invalid_upload", "form fields are too many, too large or not text") from None
            except MultipartParseError:
                raise api_error(422, "invalid_upload", "malformed multipart body") from None
        if field.seen != 1:
            raise api_error(422, "invalid_upload", "exactly one 'file' field is required")
        if field.size == 0:
            raise api_error(422, "empty_upload", "the uploaded file is empty")
        media = sniff_media(field.head)
        if media is None:
            raise api_error(415, "unsupported_media", "unsupported media type")
        storage.promote(staged, final)
    except BaseException:
        storage.remove_file(staged)
        raise

    now = clock.now()
    sha256 = field.digest.hexdigest()
    session.add(Upload(
        id=upload_id,
        organization_id=organization_id,
        sha256=sha256,
        size=field.size,
        media_family=media[0],
        media_type=media[1],
        created_at=now,
        expires_at=now + UPLOAD_TTL,
    ))
    try:
        session.commit()
    except BaseException:
        storage.remove_file(final)
        raise
    logger.info("Stored %s upload (%d bytes)", media[0], field.size)
    return {"upload_id": upload_id, "sha256": sha256, "size": field.size}, field.fields


def discard_upload(organization_id: str, upload_id: str) -> None:
    """Delete an upload that no job consumed (a rejected synchronous request)."""
    with Session(engine) as session:
        with _immediate(session):
            upload = session.get(Upload, upload_id)
            if upload is None or upload.organization_id != organization_id or upload.consumed_by_job_id is not None:
                return
            session.delete(upload)
        storage.remove_file(storage.upload_path(_storage_id(session, organization_id), upload_id))


@router.post("/v1/uploads", status_code=201)
async def create_upload(
    request: Request,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Stream multipart field ``file`` to staging, hash it, sniff its media family, promote."""
    stored, _ = await receive_upload(request, session, principal.organization_id)
    return stored


# ── Job admission ─────────────────────────────────────────────────────────

def _candidate_stage(candidate: Candidate, kind: str, settings: OrganizationSettings) -> Stage:
    """The processing stage a candidate implies. Cloud providers are egress endpoints."""
    if kind == "audio" and candidate.engine in reg.CLOUD_PROVIDERS:
        endpoint_id = f"cloud-asr:{candidate.engine}"
        registered = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
        endpoint = registered or Endpoint(id=endpoint_id, kind=EndpointKind.CLOUD_ASR, model=candidate.model)
        return Stage(name="asr", kind="asr", endpoint=endpoint)
    return Stage(name="asr" if kind == "audio" else "ocr", kind="asr" if kind == "audio" else "ocr")


def _resolve_candidates(req: JobRequest, options: dict[str, Any], settings: OrganizationSettings) -> tuple[str, list[Candidate]]:
    name = req.execution_profile or settings.default_execution_profile
    profile = settings.execution_profiles.get(name)
    if profile is None:
        raise api_error(404, "execution_profile_not_found", "no such execution profile for this organization")
    candidates = list(profile.candidates(req.kind))
    if not candidates:
        raise api_error(422, "no_candidates", "execution profile has no candidates for this job kind")
    if req.engine is not None:
        candidates = [c for c in candidates if c.engine == req.engine]
    if req.model is not None:
        candidates = [c for c in candidates if c.model == req.model]
    if not candidates:
        raise api_error(422, "unapproved_override", "engine/model override is not an approved candidate")
    ocr_engine = options.get("ocr_engine")
    if ocr_engine is not None:
        candidates = [c for c in candidates if c.ocr_engine == ocr_engine]
        if not candidates:
            raise api_error(422, "invalid_options", "ocr_engine is not available in this execution profile")
    return name, candidates


def _resolve_postprocess(session: Session, organization_id: str, req: JobRequest,
                         settings: OrganizationSettings, policy) -> tuple[dict[str, Any], bytes] | None:
    """Resolve the postprocess profile: snapshot identifiers plus the profile document.

    The document may contain LLM prompts, so it is stored as an expiring job
    artifact; the snapshot only carries ``{name, revision, sha256}``.
    """
    name = req.profile
    if name is None and settings.polish:
        name = settings.postprocess_profile
    if name is None:
        return None
    if not is_profile_name(name):
        raise api_error(422, "invalid_profile", "profile must be a stored profile name")
    try:
        profile = load_org_profile(session, organization_id, name)
    except ProfileNotFound:
        raise api_error(404, "profile_not_found", "no such profile for this organization") from None
    except ProfileError:
        raise api_error(422, "invalid_profile", "stored profile is invalid") from None
    build_pipeline(profile, policy=policy)  # raises PolicyDenied before the job exists
    row = session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id, ProfileRevision.name == name)
        .order_by(ProfileRevision.revision.desc())
    ).first()
    revision = row.revision if row is not None and not row.deleted else 0
    document = json.dumps(profile.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    meta = {"name": name, "revision": revision, "sha256": hashlib.sha256(document).hexdigest()}
    return meta, document


def _execution_snapshot(session: Session, organization_id: str, req: JobRequest,
                        options: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """Return the content-free execution snapshot and the postprocess profile document, if any."""
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError:
        raise api_error(409, "settings_missing", "organization has no settings") from None
    try:
        if not req.private and not settings.cloud_enabled:
            raise PolicyDenied("policy", "cloud processing is disabled for this organization")
        policy = settings.policy(private=req.private)
        name, candidates = _resolve_candidates(req, options, settings)
        check_policy(policy, [_candidate_stage(c, req.kind, settings) for c in candidates])
        postprocess = _resolve_postprocess(session, organization_id, req, settings, policy)
    except PolicyDenied as exc:
        raise api_error(403, "policy_denied", f"{exc.stage}: {exc.reason}") from None
    snapshot = {
        "kind": req.kind,
        "candidates": [c.model_dump(mode="json") for c in candidates],
        "language": req.language or settings.language,
        "options": options,
        "postprocess": postprocess[0] if postprocess else None,
        "policy": {
            "private": policy.private,
            "cloud_enabled": policy.cloud_enabled,
            "approved_endpoint_ids": list(policy.approved_endpoint_ids),
            "endpoints": [ep.model_dump(mode="json") for ep in policy.endpoints],
        },
        "settings_revision": revision,
        "execution_profile": name,
        "purpose": req.purpose,
        "release_id": req.release_id,
    }
    return snapshot, postprocess[1] if postprocess else None


def _request_hash(req: JobRequest, upload: Upload | None) -> str:
    """Hash the request by upload content, not upload id.

    A client that re-uploads the same file and replays the same key must get
    the same job; only a different file or different parameters conflict.
    """
    fields = req.model_dump(mode="json")
    fields["upload_id"] = None
    fields["input_sha256"] = upload.sha256 if upload is not None else req.upload_id
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _admit(organization_id: str, key: str, req: JobRequest,
           options: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Create the job, consume the upload and record the key in one locked transaction."""
    moved: tuple[Path, Path] | None = None
    profile_file: Path | None = None
    with Session(engine) as session:
        try:
            with _immediate(session):
                upload = session.get(Upload, req.upload_id)
                if upload is not None and upload.organization_id != organization_id:
                    upload = None
                request_hash = _request_hash(req, upload)
                record = session.get(IdempotencyRecord, (organization_id, key))
                if record is not None:
                    job = session.get(Job, record.job_id)
                    if record.request_hash != request_hash or job is None:
                        raise api_error(409, "idempotency_conflict",
                                        "Idempotency-Key was already used for a different request")
                    return 202, {"job_id": job.id, "status": job.status.value}

                now = clock.now()
                if (
                    upload is None
                    or upload.consumed_by_job_id is not None
                    or clock.as_utc(upload.expires_at) <= now
                ):
                    raise api_error(404, "upload_not_found", "upload not found")
                if upload.media_family != req.kind:
                    raise api_error(422, "kind_mismatch", f"upload is not {req.kind} media")

                snapshot, profile_document = _execution_snapshot(session, organization_id, req, options)
                job = Job(
                    organization_id=organization_id,
                    kind=req.kind,
                    private=req.private,
                    purpose=req.purpose,
                    status=JobStatus.PENDING,
                    execution_profile=snapshot["execution_profile"],
                    execution_snapshot=json.dumps(snapshot, sort_keys=True),
                    settings_revision=snapshot["settings_revision"],
                    upload_id=upload.id,
                    input_sha256=upload.sha256,
                    deadline_at=now + JOB_DEADLINE,
                    engine=req.engine,
                    profile=(snapshot["postprocess"] or {}).get("name"),
                    language=snapshot["language"],
                    translate=bool(options.get("translate", False)),
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
                session.flush()
                upload.consumed_by_job_id = job.id
                session.add(upload)
                session.add(IdempotencyRecord(
                    organization_id=organization_id, key=key, request_hash=request_hash,
                    job_id=job.id, created_at=now,
                ))
                storage_id = _storage_id(session, organization_id)
                src = storage.upload_path(storage_id, upload.id)
                dst = storage.job_input_path(storage_id, job.id)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(src, dst)
                except FileNotFoundError:
                    raise api_error(404, "upload_not_found", "upload not found") from None
                moved = (src, dst)
                if profile_document is not None:
                    profile_file = storage.postprocess_path(storage_id, job.id)
                    storage.write_bytes_atomic(profile_file, profile_document)
                job_id = job.id
        except BaseException:
            if profile_file is not None:
                storage.remove_file(profile_file)
            if moved is not None and moved[1].exists():
                os.replace(moved[1], moved[0])
            raise
    logger.info("Admitted %s job %s (private=%s)", req.kind, job_id, req.private)
    return 202, {"job_id": job_id, "status": JobStatus.PENDING.value}


def _validation_message(exc: ValidationError) -> str:
    fields = sorted({".".join(str(p) for p in err["loc"]) or "body" for err in exc.errors()})
    return "invalid fields: " + ", ".join(fields)


def _release_staged(release_id: str) -> bool:
    with Session(engine) as session:
        return releases.is_known(session, release_id)


@router.post("/v1/jobs", status_code=202)
async def submit_job(
    request: Request,
    principal: Principal = Depends(client_principal),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Admit a job for one upload. Same key + same request returns the same job."""
    key = (idempotency_key or "").strip()
    if not key:
        raise api_error(400, "idempotency_key_required", "Idempotency-Key header is required")
    if len(key) > MAX_IDEMPOTENCY_KEY_CHARS or not key.isprintable():
        raise api_error(400, "invalid_idempotency_key", "Idempotency-Key is too long or not printable")
    raw = await request.body()
    if len(raw) > MAX_JOB_REQUEST_BYTES:
        raise api_error(413, "too_large", "job request body is too large")
    try:
        req = JobRequest.model_validate(json.loads(raw or b"null"))
    except ValueError as exc:
        message = _validation_message(exc) if isinstance(exc, ValidationError) else "body must be JSON"
        raise api_error(422, "invalid_request", message) from None
    try:
        options = _OPTIONS_MODEL[req.kind].model_validate(req.options or {}).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise api_error(422, "invalid_options", _validation_message(exc)) from None
    if req.purpose == "evaluation" and principal.role != ROLE_ORG_ADMIN:
        raise api_error(403, "forbidden", "evaluation jobs require an org_admin credential")
    if req.purpose == "evaluation":
        if req.release_id is None or not await run_in_threadpool(_release_staged, req.release_id):
            raise api_error(422, "release_not_staged", "evaluation jobs need the release_id of a staged release")
    elif req.release_id is not None:
        raise api_error(422, "invalid_request", "release_id is only valid for evaluation jobs")

    code, body = await run_in_threadpool(_admit, principal.organization_id, key, req, options)
    return JSONResponse(body, status_code=code)


# ── Job reads and control ─────────────────────────────────────────────────

def _encode_cursor(job_id: int) -> str:
    return base64.urlsafe_b64encode(str(job_id).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    try:
        value = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise api_error(422, "invalid_cursor", "cursor is not valid") from None
    if value < 1:
        raise api_error(422, "invalid_cursor", "cursor is not valid")
    return value


@router.get("/v1/jobs")
def list_jobs(
    cursor: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's jobs, newest first, with an opaque cursor."""
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise api_error(422, "invalid_limit", f"limit must be between 1 and {MAX_PAGE_SIZE}")
    query = select(Job).where(Job.organization_id == principal.organization_id)
    if cursor is not None:
        query = query.where(Job.id < _decode_cursor(cursor))
    rows = session.exec(query.order_by(Job.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    now = clock.now()
    workers = _online_workers(session, principal.organization_id, now)
    return {
        "items": [_view(job, workers, now) for job in page],
        "next_cursor": _encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


@router.get("/v1/jobs/{job_id}")
def get_job(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _single_view(session, _owned_job(session, principal.organization_id, job_id))


@router.get("/v1/jobs/{job_id}/result", response_model=None)
def get_result(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    """Stream the accepted JobResult JSON. 409 before completion, 410 after retention or a lossy restore."""
    job = _owned_job(session, principal.organization_id, job_id)
    if job.status != JobStatus.COMPLETED:
        raise api_error(409, "not_ready", "job has not completed")
    if job.error_code == CONTENT_UNAVAILABLE:
        raise api_error(410, CONTENT_UNAVAILABLE, "result content was not restored")
    if job.result_expires_at is not None and clock.as_utc(job.result_expires_at) <= clock.now():
        raise api_error(410, "result_expired", "result was removed by retention")
    path = storage.result_path(_storage_id(session, job.organization_id), job.id)
    if not path.is_file():
        raise api_error(410, CONTENT_UNAVAILABLE, "result content is unavailable")
    return FileResponse(path, media_type="application/json")


@router.post("/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Pending → cancelled (input removed); processing → lease fenced via ``cancel_requested``."""
    removed = False
    with _immediate(session):
        job = _owned_job(session, principal.organization_id, job_id)
        now = clock.now()
        if job.status == JobStatus.PENDING:
            _finish_terminal(job, JobStatus.CANCELLED, "cancelled", now)
            job.error_message = "cancelled by client"
            removed = True
        elif job.status == JobStatus.PROCESSING and not job.cancel_requested:
            job.cancel_requested = True
            job.updated_at = now
        session.add(job)
    if removed:
        _remove_input(_storage_id(session, principal.organization_id), job_id)
    session.refresh(job)
    return _single_view(session, job)


@router.delete("/v1/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete a terminal job's row, idempotency keys and files. Non-terminal → 409."""
    with _immediate(session):
        job = _owned_job(session, principal.organization_id, job_id)
        if job.status not in _TERMINAL:
            raise api_error(409, "job_not_terminal", "cancel the job before deleting it")
        for record in session.exec(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organization_id == principal.organization_id,
                IdempotencyRecord.job_id == job_id,
            )
        ).all():
            session.delete(record)
        session.delete(job)
    storage.remove_tree(storage.job_dir(_storage_id(session, principal.organization_id), job_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
