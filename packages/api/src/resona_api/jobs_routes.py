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
import codecs
import hashlib
import json
import logging
import os
import secrets
import shutil
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from decouple import config
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlmodel import Session, or_, select
from starlette.concurrency import run_in_threadpool

from resona_job_core import (
    DEFAULT_OCR_ENGINE,
    DOCX_MEDIA_TYPE,
    MAX_INSTRUCTIONS_CHARS,
    AudioOptions,
    Candidate,
    DocumentOptions,
    Endpoint,
    EndpointKind,
    OrganizationSettings,
    PolicyDenied,
    Stage,
    TEXT_EXECUTION_PROFILE,
    TextOptions,
    check_policy,
    document_options_conflict,
)
from resona_postprocess.pipeline import build_pipeline, endpoint_ids
from resona_postprocess.profile import ProfileError

from . import clock, releases, storage
from . import engine_registry as reg
from .auth import ROLE_ORG_ADMIN, Principal, client_principal, hash_token
from .broker import JOB_DELETED, _finish_terminal, _immediate, _remove_input, endpoints_assigned, purge_job
from .db.engine import engine
from .db.models import (
    Credential,
    IdempotencyRecord,
    Job,
    JobStatus,
    Organization,
    ProfileRevision,
    Upload,
    UploadLink,
    Worker,
)
from .deps import get_db_session
from .errors import api_error, validation_message
from .privacy import ProfileNotFound, is_profile_name, load_org_profile
from .settings_store import current_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Job"])

MAX_UPLOAD_BYTES: int = config("RESONA_MAX_UPLOAD_BYTES", default=200 * 1024 * 1024, cast=int)
# Text uploads (postprocess-only jobs) are far smaller than media: 2 MiB is ~300k words.
# The worker holds the text, its output and the input copy in memory and in one result JSON.
MAX_TEXT_UPLOAD_BYTES: int = config("RESONA_MAX_TEXT_UPLOAD_BYTES", default=2 * 1024 * 1024, cast=int)
_TEXT_TYPE = ("text", "text/plain")
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_JOB_REQUEST_BYTES = 64 * 1024
UPLOAD_TTL = timedelta(hours=1)
UPLOAD_LINK_TTL = timedelta(minutes=10)
UPLOAD_LINK_HEADER = "X-Resona-Upload-Token"
# Origin that upload links point at; behind a proxy the request's own base URL is internal.
PUBLIC_URL: str = config("RESONA_PUBLIC_URL", default="")
JOB_DEADLINE = timedelta(hours=24)
WORKER_ONLINE_WINDOW = timedelta(seconds=90)
MAX_MARKDOWN_CHUNK_CHARS = 32768
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_IDEMPOTENCY_KEY_CHARS = 200
MAX_FORM_FIELDS = 32
MAX_FORM_FIELD_BYTES = 4096
_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
_SNIFF_BYTES = 16


# ── Request models ────────────────────────────────────────────────────────


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Optional[str] = Field(default=None, max_length=200)
    session_id: Optional[str] = Field(default=None, max_length=200)
    source: Optional[str] = Field(default=None, max_length=200)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["audio", "document", "text"]
    upload_id: str = Field(max_length=64)
    execution_profile: Optional[str] = Field(default=None, max_length=64)
    language: Optional[str] = Field(default=None, pattern=r"^[a-z]{2,3}$")
    engine: Optional[str] = Field(default=None, max_length=128)
    model: Optional[str] = Field(default=None, max_length=256)
    profile: Optional[str] = Field(default=None, max_length=64)
    # User instructions for the profile's llm/extract steps (user message; the step prompt stays system).
    instructions: Optional[str] = Field(default=None, min_length=1, max_length=MAX_INSTRUCTIONS_CHARS)
    options: Optional[dict[str, Any]] = None
    private: bool = True
    provenance: Optional[Provenance] = None
    purpose: Literal["production", "evaluation"] = "production"
    # Evaluation jobs name the staged release under test; workers on exactly that release run them.
    release_id: Optional[str] = Field(default=None, pattern=r"^[0-9][0-9A-Za-z.+-]{0,63}$")


_OPTIONS_MODEL: dict[str, type[BaseModel]] = {"audio": AudioOptions, "document": DocumentOptions, "text": TextOptions}


# ── Helpers ───────────────────────────────────────────────────────────────

def _storage_id(session: Session, organization_id: str) -> str:
    return session.get(Organization, organization_id).storage_id


def _owned_job(session: Session, organization_id: str, job_id: int) -> Job:
    """The caller's job; another organization's job or a deleted job's tombstone is 404."""
    job = session.get(Job, job_id)
    if job is None or job.organization_id != organization_id or job.error_code == JOB_DELETED:
        raise api_error(404, "job_not_found", "job not found")
    return job


def _iso(value: datetime | None) -> str | None:
    return None if value is None else clock.as_utc(value).isoformat()


@dataclass(frozen=True)
class OnlineWorker:
    """An approved worker seen recently, with the tuples it currently offers."""

    worker_id: str
    approved_private: bool
    capabilities: frozenset[Candidate]
    runtime_version: str | None
    text_jobs: bool = False


def _online_workers(session: Session, organization_id: str, now: datetime) -> list[OnlineWorker]:
    """Approved, unrevoked workers seen within :data:`WORKER_ONLINE_WINDOW`.

    A worker offers only tuples that are both hub-approved and reported in its latest
    heartbeat: a CUDA worker started without a usable GPU reports none, so jobs that
    only it could run wait as ``no_capable_worker``, not ``waiting_capacity``.
    """
    rows = session.exec(
        select(Worker).where(Worker.organization_id == organization_id, Worker.revoked_at.is_(None))
    ).all()
    online = []
    for worker in rows:
        if not worker.approved or worker.last_seen is None:
            continue
        if clock.as_utc(worker.last_seen) < now - WORKER_ONLINE_WINDOW:
            continue
        approved = {Candidate.model_validate(item) for item in json.loads(worker.approved_capabilities or "[]")}
        reported = {Candidate.model_validate(item) for item in json.loads(worker.reported_capabilities or "[]")}
        online.append(OnlineWorker(
            worker_id=worker.id,
            approved_private=worker.approved_private,
            capabilities=frozenset(approved & reported),
            runtime_version=worker.runtime_version,
            text_jobs=worker.text_jobs,
        ))
    return online


@dataclass(frozen=True)
class QueueContext:
    """Organization state that explains why pending jobs wait."""

    workers: list[OnlineWorker]
    approved_releases: set[tuple[str, str, str]]
    known_releases: set[str]
    settings: OrganizationSettings | None


def _queue_context(session: Session, organization_id: str, now: datetime) -> QueueContext:
    try:
        _, settings = current_settings(session, organization_id)
    except LookupError:
        settings = None
    return QueueContext(
        workers=_online_workers(session, organization_id, now),
        approved_releases=releases.approved_tuples(session, organization_id),
        known_releases=releases.known_releases(session),
        settings=settings,
    )


def _queue_reason(job: Job, ctx: QueueContext) -> str | None:
    """Why a pending job waits.

    ``no_capable_worker``: no online worker of the job's privacy class offers one of
    its candidates (with the job's worker-assigned endpoints); for a text job, none
    reports that it runs text jobs with those endpoints. ``release_not_approved``:
    such workers exist, but none runs a release approved for the job (for a text
    job: a release the hub knows, staged or bundled).
    ``waiting_capacity``: an eligible worker is online and busy.
    """
    if job.status != JobStatus.PENDING:
        return None
    snapshot = json.loads(job.execution_snapshot or "{}")
    wanted: list[Candidate | None] = (
        [None] if job.kind == "text"
        else [Candidate.model_validate(item) for item in snapshot.get("candidates", [])]
    )
    capable = [
        (worker, candidate)
        for worker in ctx.workers
        if worker.approved_private == job.private
        for candidate in wanted
        if (worker.text_jobs if candidate is None else candidate in worker.capabilities)
        and endpoints_assigned(snapshot, job.kind, candidate, worker_id=worker.worker_id, settings=ctx.settings)
    ]
    if not capable:
        return "no_capable_worker"
    runnable = any(
        releases.may_run(
            worker_release=worker.runtime_version,
            purpose=job.purpose,
            snapshot_release=snapshot.get("release_id"),
            execution_profile=job.execution_profile,
            candidate=candidate,
            approved=ctx.approved_releases,
            known=ctx.known_releases,
        )
        for worker, candidate in capable
    )
    return "waiting_capacity" if runnable else "release_not_approved"


CONTENT_UNAVAILABLE = "content_unavailable"
"""Error code of a job whose payload files did not survive a restore (``resona-admin restore``)."""


def _result_available(job: Job, now: datetime) -> bool:
    if job.status != JobStatus.COMPLETED or not job.result_manifest or job.error_code == CONTENT_UNAVAILABLE:
        return False
    return job.result_expires_at is None or clock.as_utc(job.result_expires_at) > now


def _view(job: Job, ctx: QueueContext, now: datetime) -> dict[str, Any]:
    error = None
    if job.error_code and (job.status != JobStatus.COMPLETED or job.error_code == CONTENT_UNAVAILABLE):
        error = {"code": job.error_code, "message": job.error_message or job.error_code}
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status.value,
        "queue_reason": _queue_reason(job, ctx),
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
    return _view(job, _queue_context(session, job.organization_id, now), now)


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


_ZIP_MAGIC = b"PK\x03\x04"
# The main-part content type a Word document declares; macro-enabled (.docm) and template
# (.dotx) packages declare others and stay unsupported, as do other Office/ODF containers.
_DOCX_MAIN_PART = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_CONTENT_TYPES_MAX_BYTES = 1 << 20


def sniff_docx(path: Path) -> bool:
    """True when ``path`` is a ZIP package holding a Word document: ``word/document.xml`` plus a
    ``[Content_Types].xml`` that declares the DOCX main part. ZIP magic alone is not enough."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                return False
            with archive.open("[Content_Types].xml") as handle:
                content_types = handle.read(_CONTENT_TYPES_MAX_BYTES + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError,
            NotImplementedError, ValueError, zlib.error):
        return False  # damaged, encrypted or unreadable packages are not documents
    return len(content_types) <= _CONTENT_TYPES_MAX_BYTES and _DOCX_MAIN_PART in content_types


class _TextSniffer:
    """Incremental check that a streamed file is UTF-8 text without NUL bytes.

    Text has no magic bytes, so it is the family of last resort: a file is text only
    when :func:`sniff_media` found no media and every chunk decoded. ``viable``
    turns False at the first NUL byte or invalid UTF-8 sequence.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self.viable = True

    def feed(self, chunk: bytes) -> None:
        if not self.viable:
            return
        if b"\x00" in chunk:
            self.viable = False
            return
        try:
            self._decoder.decode(chunk)
        except UnicodeDecodeError:
            self.viable = False

    def finish(self) -> bool:
        """Whether the whole stream was text (a multibyte character cut at the end is not)."""
        if self.viable:
            try:
                self._decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                self.viable = False
        return self.viable


class _TooLarge(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _BadForm(Exception):
    pass


class _FileField:
    """Multipart callbacks that stream the single ``file`` part into ``target``, hashing it.

    Other parts are small text form fields, collected into ``fields`` (bounded count and size).
    With ``text_limit`` the file may also be text (``text``): once it is past
    ``text_limit`` bytes, still valid text and not media by its magic bytes, the
    stream stops with :class:`_TooLarge` instead of reaching ``limit``.
    """

    def __init__(self, target, limit: int, *, text_limit: int | None = None) -> None:
        self._target = target
        self._limit = limit
        self._text_limit = text_limit
        self.text = _TextSniffer() if text_limit is not None else None
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
            raise _TooLarge(f"upload exceeds {self._limit} bytes")
        if len(self.head) < _SNIFF_BYTES:
            self.head += chunk[: _SNIFF_BYTES - len(self.head)]
            if self.text is not None and len(self.head) >= _SNIFF_BYTES and sniff_media(self.head) is not None:
                self.text = None  # media: the text cap does not apply
        if self.text is not None:
            self.text.feed(chunk)
            if self.text.viable and self.size > self._text_limit and sniff_media(self.head) is None:
                raise _TooLarge(f"text uploads are limited to {self._text_limit} bytes")
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


def _too_large(message: str | None = None) -> Exception:
    return api_error(413, "too_large", message or f"upload exceeds {MAX_UPLOAD_BYTES} bytes")


async def receive_upload(
    request: Request, session: Session, organization_id: str, *, accept_text: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Stream the multipart ``file`` part into org storage as one unattached upload.

    Returns ``({upload_id, sha256, size}, form_fields)``; the other multipart parts
    are returned as bounded text fields for routes that take form parameters.
    ``accept_text`` admits UTF-8 text (postprocess-only jobs) besides audio and
    document media, capped at :data:`MAX_TEXT_UPLOAD_BYTES` while streaming; routes
    that only process media leave it off.
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
            field = _FileField(target, limit, text_limit=MAX_TEXT_UPLOAD_BYTES if accept_text else None)
            parser = MultipartParser(boundary, field.callbacks())
            try:
                async for chunk in request.stream():
                    parser.write(chunk)
                parser.finalize()
            except _TooLarge as exc:
                raise _too_large(exc.message) from None
            except _BadForm:
                raise api_error(422, "invalid_upload", "form fields are too many, too large or not text") from None
            except MultipartParseError:
                raise api_error(422, "invalid_upload", "malformed multipart body") from None
        if field.seen != 1:
            raise api_error(422, "invalid_upload", "exactly one 'file' field is required")
        if field.size == 0:
            raise api_error(422, "empty_upload", "the uploaded file is empty")
        media = sniff_media(field.head)
        if media is None and field.head.startswith(_ZIP_MAGIC) and sniff_docx(staged):
            media = ("document", DOCX_MEDIA_TYPE)
        if media is None and field.text is not None and field.text.finish():
            media = _TEXT_TYPE
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
    stored, _ = await receive_upload(request, session, principal.organization_id, accept_text=True)
    return stored


def _consume_upload_link(session: Session, token: Optional[str]) -> str:
    """Mark the link used and return its organization; 401 for any unusable link.

    The link is spent by the first attempt, even one that later fails validation,
    so a leaked token can never deliver a second file.
    """
    if not token:
        raise api_error(401, "invalid_upload_link", f"missing {UPLOAD_LINK_HEADER} header")
    with _immediate(session):
        link = session.get(UploadLink, hash_token(token.strip()))
        now = clock.now()
        usable = (
            link is not None
            and link.used_at is None
            and clock.as_utc(link.expires_at) > clock.as_utc(now)
        )
        if usable:
            credential = session.get(Credential, link.credential_id)
            usable = credential is not None and credential.revoked_at is None
        if not usable:
            raise api_error(401, "invalid_upload_link", "upload link is unknown, used, expired or revoked")
        link.used_at = now
        session.add(link)
        organization_id = link.organization_id
    return organization_id


@router.post("/v1/upload-links", status_code=201)
def create_upload_link(
    request: Request,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Mint a single-use link for one upload, valid ten minutes.

    For callers that cannot hold the client credential, such as an agent sandbox
    behind an MCP server: the file is sent with ``curl`` and its bytes never pass
    through a model. Only the token's hash is stored.
    """
    token = "upt_" + secrets.token_urlsafe(32)
    now = clock.now()
    expires_at = now + UPLOAD_LINK_TTL
    session.add(UploadLink(
        token_hash=hash_token(token),
        organization_id=principal.organization_id,
        credential_id=principal.credential_id,
        created_at=now,
        expires_at=expires_at,
    ))
    session.commit()
    base = (PUBLIC_URL or str(request.base_url)).rstrip("/")
    return {
        "upload_url": f"{base}/v1/upload-links/upload",
        "token_header": UPLOAD_LINK_HEADER,
        "token": token,
        "expires_at": _iso(expires_at),
        "max_bytes": MAX_UPLOAD_BYTES,
    }


@router.post("/v1/upload-links/upload", status_code=201)
async def upload_with_link(
    request: Request,
    upload_token: Optional[str] = Header(default=None, alias=UPLOAD_LINK_HEADER),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Spend an upload link: same multipart ``file`` contract as ``POST /v1/uploads``."""
    organization_id = _consume_upload_link(session, upload_token)
    stored, _ = await receive_upload(request, session, organization_id, accept_text=True)
    return stored


# ── Job admission ─────────────────────────────────────────────────────────

def _candidate_stage(candidate: Candidate, kind: str, settings: OrganizationSettings) -> Stage:
    """The processing stage a candidate implies. Cloud providers are egress endpoints."""
    if kind == "audio" and candidate.engine in reg.CLOUD_PROVIDERS:
        endpoint_id = reg.cloud_asr_endpoint_id(candidate.engine)
        registered = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
        endpoint = registered or Endpoint(id=endpoint_id, kind=EndpointKind.CLOUD_ASR, model=candidate.model)
        return Stage(name="asr", kind="asr", endpoint=endpoint)
    return Stage(name="asr" if kind == "audio" else "ocr", kind="asr" if kind == "audio" else "ocr")


def _resolve_candidates(req: JobRequest, options: dict[str, Any], settings: OrganizationSettings,
                        media_type: str | None) -> tuple[str, list[Candidate]]:
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
    # A DOCX job runs no OCR: _option_compatible refuses ocr_engine there with the DOCX reason.
    if ocr_engine is not None and media_type != DOCX_MEDIA_TYPE:
        candidates = [c for c in candidates if c.ocr_engine == ocr_engine]
        if not candidates:
            raise api_error(422, "invalid_options", "ocr_engine is not available in this execution profile")
    if req.kind == "document":
        candidates = _option_compatible(candidates, options, req.language or settings.language, media_type)
    return name, candidates


def _option_compatible(candidates: list[Candidate], options: dict[str, Any], language: str,
                       media_type: str | None) -> list[Candidate]:
    """The document candidates whose OCR engine can run ``options``; 422 if none can.

    Resolves options as the worker does (the job language when options name none, the
    engine's default OCR engine when a candidate pins none), so the snapshot only offers
    candidates whose workers will not reject the job after claiming it. A DOCX upload runs
    no OCR, so its candidates are not filtered by OCR engine: any docling candidate serves it.
    """
    try:
        resolved = DocumentOptions.model_validate({"languages": [language], **options})
    except ValidationError:
        raise api_error(422, "invalid_options", "the job language is not supported for documents") from None
    compatible: list[Candidate] = []
    conflicts: list[str] = []
    for candidate in candidates:
        conflict = document_options_conflict(
            resolved, candidate.ocr_engine or DEFAULT_OCR_ENGINE, media_type=media_type
        )
        if conflict is None:
            compatible.append(candidate)
        elif conflict not in conflicts:
            conflicts.append(conflict)
    if not compatible:
        raise api_error(422, "invalid_options", "; ".join(conflicts))
    return compatible


def _resolve_postprocess(session: Session, organization_id: str, req: JobRequest,
                         settings: OrganizationSettings, policy) -> tuple[dict[str, Any], bytes, list[str]] | None:
    """Resolve the postprocess profile: snapshot identifiers, the profile document and
    the endpoint IDs its LLM steps call.

    The document may contain LLM prompts, so it is stored as an expiring job
    artifact; the snapshot only carries ``{name, revision, sha256}``.
    """
    name = req.profile
    if name is None and settings.polish:
        name = settings.postprocess_profile
    if name is None:
        if req.instructions is not None:
            raise api_error(422, "invalid_instructions", "instructions need a postprocess profile")
        return None
    if not is_profile_name(name):
        raise api_error(422, "invalid_profile", "profile must be a stored profile name")
    try:
        profile = load_org_profile(session, organization_id, name)
    except ProfileNotFound:
        raise api_error(404, "profile_not_found", "no such profile for this organization") from None
    except ProfileError:
        raise api_error(422, "invalid_profile", "stored profile is invalid") from None
    if req.instructions is not None:
        try:
            profile = profile.with_instructions(req.instructions)
        except ProfileError as exc:
            raise api_error(422, "invalid_instructions", str(exc)) from None
    build_pipeline(profile, policy=policy)  # raises PolicyDenied before the job exists
    row = session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id, ProfileRevision.name == name)
        .order_by(ProfileRevision.revision.desc())
    ).first()
    revision = row.revision if row is not None and not row.deleted else 0
    document = json.dumps(profile.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    meta = {"name": name, "revision": revision, "sha256": hashlib.sha256(document).hexdigest()}
    return meta, document, endpoint_ids(profile)


def _snapshot_body(*, kind: str, candidates: list[Candidate], language: str, options: dict[str, Any],
                   postprocess: tuple[dict[str, Any], bytes, list[str]] | None, policy, revision: int,
                   execution_profile: str, purpose: str, release_id: str | None) -> dict[str, Any]:
    """The content-free execution snapshot a job carries from admission to the worker."""
    return {
        "kind": kind,
        "candidates": [c.model_dump(mode="json") for c in candidates],
        "language": language,
        "options": options,
        "postprocess": postprocess[0] if postprocess else None,
        "endpoint_ids": postprocess[2] if postprocess else [],
        "policy": {
            "private": policy.private,
            "cloud_enabled": policy.cloud_enabled,
            "approved_endpoint_ids": list(policy.approved_endpoint_ids),
            "endpoints": [ep.model_dump(mode="json") for ep in policy.endpoints],
        },
        "settings_revision": revision,
        "execution_profile": execution_profile,
        "purpose": purpose,
        "release_id": release_id,
    }


def _execution_snapshot(session: Session, organization_id: str, req: JobRequest,
                        options: dict[str, Any], media_type: str | None) -> tuple[dict[str, Any], bytes | None]:
    """Return the content-free execution snapshot and the postprocess profile document, if any."""
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError:
        raise api_error(409, "settings_missing", "organization has no settings") from None
    try:
        if not req.private and not settings.cloud_enabled:
            raise PolicyDenied("policy", "cloud processing is disabled for this organization")
        policy = settings.policy(private=req.private)
        if req.kind == "text":
            # No engine stage: the postprocess profile is the job; build_pipeline checks its stages.
            name, candidates = TEXT_EXECUTION_PROFILE, []
        else:
            name, candidates = _resolve_candidates(req, options, settings, media_type)
            check_policy(policy, [_candidate_stage(c, req.kind, settings) for c in candidates])
        postprocess = _resolve_postprocess(session, organization_id, req, settings, policy)
    except PolicyDenied as exc:
        raise api_error(403, "policy_denied", f"{exc.stage}: {exc.reason}") from None
    snapshot = _snapshot_body(
        kind=req.kind, candidates=candidates, language=req.language or settings.language, options=options,
        postprocess=postprocess, policy=policy, revision=revision, execution_profile=name,
        purpose=req.purpose, release_id=req.release_id,
    )
    return snapshot, postprocess[1] if postprocess else None


def _link_or_copy(source: Path, dest: Path) -> None:
    """Give a job its own name for stored bytes: a hard link, or a copy where links are unavailable."""
    try:
        os.link(source, dest)
        return
    except FileNotFoundError:
        raise api_error(410, CONTENT_UNAVAILABLE, "stored content is unavailable") from None
    except OSError:
        pass
    staged = storage.staging_path(dest)
    try:
        shutil.copyfile(source, staged)
        storage.promote(staged, dest)
    except BaseException:
        storage.remove_file(staged)
        raise


TESTBENCH_PURPOSE = "testbench"
TESTBENCH_PROFILE = "testbench"
"""``Job.execution_profile`` of testbench jobs: they are pinned to one candidate, not drawn from a profile."""


def admit_testbench_job(session: Session, organization_id: str, *, kind: str, language: str,
                        candidate: Candidate, source: Path, sha256: str, media_type: str | None) -> Job:
    """Create a private ``testbench`` job for stored item bytes, pinned to exactly ``candidate``.

    Runs inside the caller's write transaction and does not commit. The candidate
    bypasses execution-profile membership (the caller proves it is one an approved
    worker has tested), but the policy check still runs and the snapshot is private with
    no postprocess profile. The job's input is a link to (or copy of) ``source``, so the
    item's file survives the job's own input removal.
    """
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError:
        raise api_error(409, "settings_missing", "organization has no settings") from None
    try:
        policy = settings.policy(private=True)
        check_policy(policy, [_candidate_stage(candidate, kind, settings)])
    except PolicyDenied as exc:
        raise api_error(403, "policy_denied", f"{exc.stage}: {exc.reason}") from None
    options = _OPTIONS_MODEL[kind].model_validate({}).model_dump(mode="json", exclude_none=True)
    if kind == "document":
        _option_compatible([candidate], options, language, media_type)
    snapshot = _snapshot_body(
        kind=kind, candidates=[candidate], language=language, options=options, postprocess=None,
        policy=policy, revision=revision, execution_profile=TESTBENCH_PROFILE,
        purpose=TESTBENCH_PURPOSE, release_id=None,
    )
    now = clock.now()
    job = Job(
        organization_id=organization_id,
        kind=kind,
        private=True,
        purpose=TESTBENCH_PURPOSE,
        status=JobStatus.PENDING,
        execution_profile=TESTBENCH_PROFILE,
        execution_snapshot=json.dumps(snapshot, sort_keys=True),
        settings_revision=revision,
        input_sha256=sha256,
        deadline_at=now + JOB_DEADLINE,
        engine=candidate.engine,
        language=language,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    dest = storage.job_input_path(_storage_id(session, organization_id), job.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _link_or_copy(source, dest)
    except BaseException:
        storage.remove_tree(dest.parent)
        raise
    return job


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

                snapshot, profile_document = _execution_snapshot(
                    session, organization_id, req, options, upload.media_type
                )
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


def _release_staged(release_id: str) -> bool:
    with Session(engine) as session:
        return releases.is_known(session, release_id)


def _check_text_request(req: JobRequest) -> None:
    """A text job runs one stored postprocess profile and nothing else."""
    if req.profile is None:
        raise api_error(422, "profile_required", "text jobs need a postprocess profile name")
    if req.execution_profile is not None or req.engine is not None or req.model is not None:
        raise api_error(422, "invalid_request",
                        "text jobs run no engine; execution_profile, engine and model are not accepted")
    if req.purpose != "production":
        raise api_error(422, "invalid_request", "evaluation jobs are audio or document jobs")


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
        message = validation_message(exc) if isinstance(exc, ValidationError) else "body must be JSON"
        raise api_error(422, "invalid_request", message) from None
    try:
        options = _OPTIONS_MODEL[req.kind].model_validate(req.options or {}).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise api_error(422, "invalid_options", validation_message(exc)) from None
    if req.kind == "text":
        _check_text_request(req)
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


def jobs_page(session: Session, organization_id: str, cursor: Optional[str], limit: int) -> dict[str, Any]:
    """One page of an organization's jobs, newest first, with an opaque cursor."""
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise api_error(422, "invalid_limit", f"limit must be between 1 and {MAX_PAGE_SIZE}")
    query = select(Job).where(
        Job.organization_id == organization_id,
        or_(Job.error_code.is_(None), Job.error_code != JOB_DELETED),
    )
    if cursor is not None:
        query = query.where(Job.id < _decode_cursor(cursor))
    rows = session.exec(query.order_by(Job.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    now = clock.now()
    ctx = _queue_context(session, organization_id, now)
    return {
        "items": [_view(job, ctx, now) for job in page],
        "next_cursor": _encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


@router.get("/v1/jobs")
def list_jobs(
    cursor: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's jobs, newest first, with an opaque cursor."""
    return jobs_page(session, principal.organization_id, cursor, limit)


@router.get("/v1/jobs/{job_id}")
def get_job(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _single_view(session, _owned_job(session, principal.organization_id, job_id))


def _result_file(session: Session, job: Job) -> Path:
    """The accepted result file. 409 before completion, 410 after retention or a lossy restore."""
    if job.status != JobStatus.COMPLETED:
        raise api_error(409, "not_ready", "job has not completed")
    if job.error_code == CONTENT_UNAVAILABLE:
        raise api_error(410, CONTENT_UNAVAILABLE, "result content was not restored")
    if job.result_expires_at is not None and clock.as_utc(job.result_expires_at) <= clock.now():
        raise api_error(410, "result_expired", "result was removed by retention")
    path = storage.result_path(_storage_id(session, job.organization_id), job.id)
    if not path.is_file():
        raise api_error(410, CONTENT_UNAVAILABLE, "result content is unavailable")
    return path


@router.get("/v1/jobs/{job_id}/result", response_model=None)
def get_result(
    job_id: int,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    """Stream the accepted JobResult JSON. 409 before completion, 410 after retention or a lossy restore."""
    path = _result_file(session, _owned_job(session, principal.organization_id, job_id))
    return FileResponse(path, media_type="application/json")


@router.get("/v1/jobs/{job_id}/result/markdown")
def get_result_markdown(
    job_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=MAX_MARKDOWN_CHUNK_CHARS, ge=1, le=MAX_MARKDOWN_CHUNK_CHARS),
    source: Literal["markdown", "raw"] = Query(default="markdown"),
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """One character range of the result's Markdown plus small result metadata,
    never the whole result.

    ``source=raw`` reads ``raw_markdown`` (the text before postprocessing) when the
    result has one, else ``markdown``; the response's ``source`` names the field read.
    ``next_offset`` is null once the range reaches the end. Same 404/409/410 rules as
    the full result; an offset past the end returns empty text.
    """
    path = _result_file(session, _owned_job(session, principal.organization_id, job_id))
    try:
        result = json.loads(path.read_bytes())
        field = "raw_markdown" if source == "raw" and result.get("raw_markdown") is not None else "markdown"
        markdown = result.get(field) or ""
    except (OSError, ValueError, AttributeError):
        raise api_error(410, CONTENT_UNAVAILABLE, "result content is unavailable") from None
    total = len(markdown)
    end = min(offset + limit, total)
    return {
        "text": markdown[offset:end] if offset < total else "",
        "offset": offset,
        "next_offset": end if end < total else None,
        "total_chars": total,
        "source": field,
        "kind": result.get("kind"),
        "language": result.get("language"),
        "page_count": len(result.get("pages") or []),
        "warnings": list(result.get("warnings") or []),
        "provenance": result.get("provenance") or {},
    }


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
    """Delete a job in any state; later reads are 404 at once.

    A pending or terminal job's row, idempotency keys and files go now. A processing
    job is cancelled first: its lease is fenced (the worker's next call is
    ``stale_lease``), its input removed, and its row kept as an invisible
    ``deleted`` tombstone until the replaced lease would have expired, so a late
    worker cannot resurrect it; the maintenance sweep then purges it.
    """
    storage_id = _storage_id(session, principal.organization_id)
    with _immediate(session):
        job = _owned_job(session, principal.organization_id, job_id)
        for record in session.exec(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organization_id == principal.organization_id,
                IdempotencyRecord.job_id == job_id,
            )
        ).all():
            session.delete(record)
        purge_job(session, job)
    storage.remove_tree(storage.job_dir(storage_id, job_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
