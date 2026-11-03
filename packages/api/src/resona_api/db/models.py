import logging
from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import Field, SQLModel, Column
from sqlalchemy import Boolean, DateTime, Integer, String, Text, text
from .. import clock

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return clock.now()


class JobStatus(str, Enum):
    """Status of a transcription job."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Organization(SQLModel, table=True):
    """A tenant. ``id`` is opaque (SKAILE ``organizationId`` when integrated)."""

    id: str = Field(primary_key=True)
    storage_id: str = Field(unique=True, index=True)  # uuid4 hex, used for on-disk paths
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class Worker(SQLModel, table=True):
    """A pull worker enrolled in one organization."""

    id: str = Field(primary_key=True)
    organization_id: str = Field(foreign_key="organization.id", index=True)
    name: str
    approved: bool = Field(default=False)
    approved_private: bool = Field(default=False)
    approved_capabilities: str = Field(default="[]")  # JSON list of Candidate dicts
    reported_capabilities: str = Field(default="[]")  # JSON list of Candidate dicts
    last_seen: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    runtime_version: Optional[str] = Field(default=None)
    revoked_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class Credential(SQLModel, table=True):
    """Stores only the SHA-256 hash of an issued bearer token."""

    id: str = Field(primary_key=True)  # "cred_" + token_hex(8)
    secret_hash: str = Field(unique=True, index=True)
    organization_id: str = Field(foreign_key="organization.id", index=True)
    role: str  # client | org_admin | worker
    worker_id: Optional[str] = Field(default=None, foreign_key="worker.id")
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
    revoked_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class SettingsRevision(SQLModel, table=True):
    """Immutable organization settings document, one row per revision."""

    organization_id: str = Field(foreign_key="organization.id", primary_key=True)
    revision: int = Field(primary_key=True)
    settings_json: str
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class ProfileRevision(SQLModel, table=True):
    """Revisioned postprocess profile owned by an organization."""

    organization_id: str = Field(foreign_key="organization.id", primary_key=True)
    name: str = Field(primary_key=True)
    revision: int = Field(primary_key=True)
    profile_json: str
    deleted: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class Job(SQLModel, table=True):
    """A durable job: org-bound input, immutable execution snapshot, fenced lease.

    Content never lives in this row. Inputs and results are artifact files under
    ``storage.job_dir``; the row holds state, hashes and expiry only.
    ``execution_snapshot`` is JSON of approved identifiers and non-content options.
    Columns that are NOT NULL carry ``server_default`` so raw inserts (migration 1
    copies legacy rows with SQL) remain valid.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: str = Field(foreign_key="organization.id", index=True, nullable=False)
    kind: str = Field(default="audio", sa_column=Column(String, nullable=False, server_default="audio"))
    private: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    purpose: str = Field(default="production", sa_column=Column(String, nullable=False, server_default="production"))
    status: JobStatus = Field(default=JobStatus.PENDING)
    execution_profile: str = Field(
        default="private-auto", sa_column=Column(String, nullable=False, server_default="private-auto")
    )
    execution_snapshot: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))
    candidate: Optional[str] = Field(default=None)
    settings_revision: Optional[int] = Field(default=None)
    upload_id: Optional[str] = Field(default=None, index=True)
    input_sha256: Optional[str] = Field(default=None)
    input_deleted: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="0"))
    worker_id: Optional[str] = Field(default=None, index=True)
    attempt: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    max_attempts: int = Field(default=3, sa_column=Column(Integer, nullable=False, server_default="3"))
    lease_token_hash: Optional[str] = Field(default=None)
    lease_until: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    deadline_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False, server_default=text("(datetime('now', '+1 day'))"))
    )
    cancel_requested: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="0"))
    error_code: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    result_manifest: Optional[str] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    result_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    engine: Optional[str] = Field(default=None)
    profile: Optional[str] = Field(default=None)
    language: Optional[str] = Field(default=None)
    translate: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="0"))
    created_at: datetime = Field(
        default_factory=lambda: clock.now(),
        sa_column=Column(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: clock.now(),
        sa_column=Column(DateTime, nullable=False),
    )


class Upload(SQLModel, table=True):
    """An org-bound staged input. Expires one hour after creation unless consumed by one job."""

    id: str = Field(primary_key=True)  # "upl_" + token_hex(12)
    organization_id: str = Field(foreign_key="organization.id", index=True, nullable=False)
    sha256: str
    size: int
    media_family: str  # audio | document
    media_type: str
    created_at: datetime = Field(
        default_factory=lambda: clock.now(),
        sa_column=Column(DateTime, nullable=False),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    consumed_by_job_id: Optional[int] = Field(default=None)


class UploadLink(SQLModel, table=True):
    """A single-use capability to create one upload without the client credential.

    Minted by a client credential, valid ten minutes, consumed by the first upload
    attempt. Only the token's SHA-256 is stored; revoking the minting credential
    invalidates its unused links.
    """

    token_hash: str = Field(primary_key=True)
    organization_id: str = Field(foreign_key="organization.id", index=True, nullable=False)
    credential_id: str = Field(foreign_key="credential.id", nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: clock.now(),
        sa_column=Column(DateTime, nullable=False),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))



class IdempotencyRecord(SQLModel, table=True):
    """Maps an org-scoped ``Idempotency-Key`` to the job it created. Kept seven days."""

    organization_id: str = Field(foreign_key="organization.id", primary_key=True)
    key: str = Field(primary_key=True)
    request_hash: str
    job_id: int
    created_at: datetime = Field(
        default_factory=lambda: clock.now(),
        sa_column=Column(DateTime, nullable=False),
    )


class Release(SQLModel, table=True):
    """A release manifest known to the hub.

    ``staged`` admits the release for evaluation jobs only; production use needs an
    :class:`ApprovedReleaseTuple`. ``bundled`` is the release the hub itself ships;
    it needs no staging for evaluation jobs but, like any release, an approval for production.
    """

    release_id: str = Field(primary_key=True)
    status: str = Field(sa_column=Column(String, nullable=False, server_default="staged"))
    manifest_json: str = Field(sa_column=Column(Text, nullable=False))
    manifest_sha256: str
    staged_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class Evaluation(SQLModel, table=True):
    """A recorded evaluation summary: metrics and provenance only, never content."""

    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: str = Field(foreign_key="organization.id", index=True, nullable=False)
    profile: str
    execution_profile: str
    platform: str
    release_id: str
    corpus_sha256: str
    baseline_revision: Optional[int] = Field(default=None)
    fixtures_json: str = Field(sa_column=Column(Text, nullable=False))
    gate_json: str = Field(sa_column=Column(Text, nullable=False))
    promoted_revision: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class Baseline(SQLModel, table=True):
    """Revisioned baseline per (organization, execution profile, platform); the highest revision is current."""

    organization_id: str = Field(foreign_key="organization.id", primary_key=True)
    execution_profile: str = Field(primary_key=True)
    platform: str = Field(primary_key=True)
    revision: int = Field(primary_key=True)
    evaluation_id: int = Field(foreign_key="evaluation.id")
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )


class ApprovedReleaseTuple(SQLModel, table=True):
    """A release approved for production jobs of one organization's (execution profile, platform)."""

    organization_id: str = Field(foreign_key="organization.id", primary_key=True)
    execution_profile: str = Field(primary_key=True)
    platform: str = Field(primary_key=True)
    release_id: str = Field(foreign_key="release.release_id", primary_key=True)
    evaluation_id: int = Field(foreign_key="evaluation.id")
    approved_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime, nullable=False),
    )
