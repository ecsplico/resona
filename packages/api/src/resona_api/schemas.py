"""Response and error models of the client-facing routes, as published in ``/openapi.json``.

The models describe the bodies the routes already return; they add no field and
change no value. Bodies that pass a stored document through (a result's
provenance, a profile) are declared with ``response_model_exclude_unset`` on the
route, so a key the stored document lacks stays absent.

:func:`error_responses` builds a route's ``responses=`` entry: every error status
with its body model and its documented codes (``x-error-codes``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema
from resona_job_core import Candidate, JobResult, OrganizationSettings
from resona_job_core.results import Provenance, Segment

JobKind = Literal["audio", "document", "text"]
JobStatusName = Literal["pending", "processing", "completed", "failed", "cancelled"]
QueueReason = Literal["no_capable_worker", "release_not_approved", "worker_outdated", "waiting_capacity"]


# ── Errors ────────────────────────────────────────────────────────────────


class ErrorBody(BaseModel):
    """The hub's error body. ``error`` is the machine-readable code."""

    error: str
    message: str


class JobErrorBody(ErrorBody):
    """Error of the synchronous transcription adapter; ``job_id`` names the job once one was admitted."""

    job_id: Optional[int] = None


class DetailErrorBody(BaseModel):
    """Error without a code, on the few routes that predate :class:`ErrorBody`."""

    detail: str


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    loc: list[Union[str, int]]
    msg: str
    type: str


class ValidationErrorBody(BaseModel):
    """Rejected path, query or body parameters (the framework's own validation)."""

    detail: list[ValidationIssue]


DETAIL = "detail"
"""Marker in an :func:`error_responses` code list: this status answers ``{"detail": "<text>"}``."""
VALIDATION = "validation"
"""Marker in an :func:`error_responses` code list: this status also answers :class:`ValidationErrorBody`."""

_AUTH = {401: ["unauthorized"], 403: ["forbidden"]}


def error_responses(
    codes: dict[int, list[str]] | None = None, *, auth: bool = True, model: type[BaseModel] = ErrorBody,
) -> dict[int | str, dict[str, Any]]:
    """``responses=`` for a route: per error status the body model and its codes.

    ``auth`` adds 401 ``unauthorized`` and 403 ``forbidden`` (wrong role) of the
    credential check. A code list may hold :data:`DETAIL` and :data:`VALIDATION`
    for the bodies that are not ``model``.
    """
    merged = merge_codes(_AUTH if auth else {}, codes or {})
    responses: dict[int | str, dict[str, Any]] = {}
    for status, names in sorted(merged.items()):
        error_codes = [n for n in names if n not in (DETAIL, VALIDATION)]
        bodies: list[Any] = [model] if error_codes else []
        described = [f"`{code}`" for code in error_codes]
        if DETAIL in names:
            bodies.append(DetailErrorBody)
            described.append("an uncoded `detail` message")
        if VALIDATION in names:
            bodies.append(ValidationErrorBody)
            described.append("rejected parameters")
        entry: dict[str, Any] = {"model": Union[tuple(bodies)], "description": "Error: " + ", ".join(described) + "."}
        if error_codes:
            entry["x-error-codes"] = error_codes
        responses[status] = entry
    return responses


JOB_ID = {404: ["job_not_found"], 422: [VALIDATION]}
"""Errors of every ``/v1/jobs/{job_id}…`` route."""
RESULT = {**JOB_ID, 409: ["not_ready"], 410: ["result_expired", "content_unavailable"]}
UPLOAD = {413: ["too_large"], 415: ["unsupported_media"], 422: ["invalid_upload", "empty_upload"]}
"""Errors of a multipart ``file`` upload."""
ADMISSION = {
    403: ["policy_denied"],
    404: ["upload_not_found", "execution_profile_not_found", "profile_not_found"],
    409: ["idempotency_conflict", "settings_missing"],
    422: [
        "invalid_request", "invalid_options", "invalid_instructions", "invalid_profile", "kind_mismatch",
        "no_candidates", "unapproved_override",
    ],
}
"""Errors of job admission, shared by ``POST /v1/jobs`` and the synchronous adapter."""


def merge_codes(*sources: dict[int, list[str]]) -> dict[int, list[str]]:
    merged: dict[int, list[str]] = {}
    for source in sources:
        for status, names in source.items():
            merged.setdefault(status, []).extend(n for n in names if n not in merged[status])
    return merged


# ── Uploads ───────────────────────────────────────────────────────────────


class UploadView(BaseModel):
    upload_id: str
    sha256: str
    size: int


class UploadLinkView(BaseModel):
    """A single-use upload link: send multipart ``file`` to ``upload_url`` with header ``token_header: token``."""

    upload_url: str
    token_header: str
    token: str
    expires_at: str
    max_bytes: int


# ── Jobs ──────────────────────────────────────────────────────────────────


class JobAccepted(BaseModel):
    job_id: int
    status: JobStatusName


class JobError(BaseModel):
    code: str
    message: str


class JobView(BaseModel):
    job_id: int
    kind: JobKind
    status: JobStatusName
    queue_reason: Optional[QueueReason]
    private: bool
    execution_profile: Optional[str]
    candidate: Optional[Candidate]
    attempt: int
    error: Optional[JobError]
    result_available: bool
    created_at: Optional[str]
    updated_at: Optional[str]
    deadline_at: Optional[str]


class JobPage(BaseModel):
    items: list[JobView]
    next_cursor: Optional[str]


class MarkdownChunk(BaseModel):
    """One character range of a result's Markdown; ``source`` names the field read."""

    text: str
    offset: int
    next_offset: Optional[int]
    total_chars: int
    source: Literal["markdown", "raw_markdown"]
    kind: Optional[JobKind]
    language: Optional[str]
    page_count: int
    warnings: list[str]
    provenance: Provenance


# ── Engines ───────────────────────────────────────────────────────────────


class EngineCandidate(BaseModel):
    candidate: Candidate
    execution_profiles: list[str]


class EngineView(BaseModel):
    name: str
    kind: str
    job_kind: Literal["audio", "document", "speech"]
    capabilities: list[str]
    private: bool
    available: bool
    models: list[str]
    candidates: list[EngineCandidate]


class EngineList(BaseModel):
    engines: list[EngineView]
    default: Optional[str]


class ListenEngineView(BaseModel):
    name: str
    kind: str
    private: bool
    models: list[str]


class ListenEngineList(BaseModel):
    engines: list[ListenEngineView]
    default: Optional[str]


# ── Audio ─────────────────────────────────────────────────────────────────


class TranscriptionResponse(BaseModel):
    """OpenAI-compatible transcription. ``language``, ``duration`` and ``segments`` only with
    ``response_format=verbose_json``; ``structured`` only when the profile extracted data."""

    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: Optional[list[Segment]] = None
    structured: Optional[dict[str, Any]] = None


# ── Profiles and settings ─────────────────────────────────────────────────


class ProfileRef(BaseModel):
    name: str
    revision: int


class ProfileList(BaseModel):
    profiles: list[ProfileRef]


class ProfileDocument(BaseModel):
    """A postprocess profile; see the profile file format. ``instructions`` only when set."""

    name: str
    # Stored profiles never had their description type-checked; pass it through as it is.
    description: Annotated[Any, WithJsonSchema({"type": "string"})]
    initial_prompt: list[str]
    steps: list[dict[str, Any]]
    instructions: Optional[str] = None


class ProfileView(BaseModel):
    name: str
    revision: int
    profile: ProfileDocument


class Deleted(BaseModel):
    ok: bool


class SettingsDoc(BaseModel):
    organization_id: str
    revision: int
    settings: OrganizationSettings


# ── Version and probes ────────────────────────────────────────────────────


class VersionView(BaseModel):
    release_id: str
    hub_version: str
    worker_protocol: int


class HealthView(BaseModel):
    status: Literal["ok"]


class ReadyChecks(BaseModel):
    schema_: bool = Field(alias="schema")
    data_writable: bool
    maintenance: bool


class ReadyView(BaseModel):
    """200 when every check holds, else 503 with the same body."""

    status: Literal["ready", "not_ready"]
    checks: ReadyChecks
    capacity: Literal["available", "degraded"]


__all__ = [
    "ADMISSION", "DETAIL", "JOB_ID", "RESULT", "UPLOAD", "VALIDATION",
    "Deleted", "DetailErrorBody", "EngineList", "ErrorBody", "HealthView", "JobAccepted", "JobErrorBody",
    "JobPage", "JobResult", "JobView", "ListenEngineList", "MarkdownChunk", "ProfileList", "ProfileRef",
    "ProfileView", "ReadyView", "SettingsDoc", "TranscriptionResponse", "UploadLinkView", "UploadView",
    "ValidationErrorBody", "VersionView", "error_responses", "merge_codes",
]
