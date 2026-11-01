"""Releases on the hub: identity, staging, and which worker releases may run which jobs.

Release identifiers are package versions (tag ``v0.2.0`` → release ``0.2.0``). A
worker's release is the ``runtime_version`` it reports in its heartbeat (its
installed ``resona-worker`` version). The hub's own release, ``RESONA_RELEASE_ID``
or else the installed ``resona-api`` version, is the *bundled* release: recorded
at startup so it can run evaluation jobs without being staged first. Every
release, bundled or staged, runs production jobs only for the (execution profile,
platform) tuples approved by promoting a measured evaluation; a release without
such an approval runs evaluation jobs only.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata
from typing import Literal

from decouple import config
from pydantic import ValidationError
from sqlmodel import Session, select

from resona_job_core import Candidate
from resona_job_core.release import ReleaseManifest

from . import clock
from .db.models import ApprovedReleaseTuple, Release
from .errors import validation_message

Platform = Literal["mac-metal", "linux-cuda", "cpu"]
PLATFORMS: tuple[str, ...] = ("mac-metal", "linux-cuda", "cpu")
_PLATFORM_OF_ACCELERATOR = {"metal": "mac-metal", "cuda": "linux-cuda", "cpu": "cpu"}

STATUS_STAGED = "staged"
STATUS_BUNDLED = "bundled"


class ReleaseError(ValueError):
    """A manifest is invalid or conflicts with a stored release."""


def hub_version() -> str:
    try:
        return metadata.version("resona-api")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def hub_release_id() -> str:
    """The release this hub ships (admissible for evaluation jobs without staging)."""
    return config("RESONA_RELEASE_ID", default="").strip() or hub_version()


def platform_of(candidate: Candidate) -> str:
    return _PLATFORM_OF_ACCELERATOR[candidate.accelerator]


def parse_manifest(raw: bytes) -> ReleaseManifest:
    try:
        return ReleaseManifest.model_validate_json(raw)
    except ValidationError as exc:
        raise ReleaseError(validation_message(exc, prefix="invalid release manifest: ", root="manifest")) from None


def _bundled_placeholder(release_id: str) -> str:
    return json.dumps({"release_id": release_id, "bundled": True}, sort_keys=True)


def stage_release(session: Session, raw: bytes) -> Release:
    """Store a validated manifest as ``staged``. Re-staging identical bytes is a no-op.

    The hub's own (bundled) release starts with a placeholder instead of a manifest;
    staging its real manifest fills it in and keeps the ``bundled`` status, so workers
    can verify and install exactly what the hub image was released with.
    """
    manifest = parse_manifest(raw)
    canonical = manifest.canonical_json()
    digest = manifest.digest()
    existing = session.get(Release, manifest.release_id)
    if existing is not None:
        if existing.manifest_sha256 == digest:
            return existing
        if existing.status != STATUS_BUNDLED or existing.manifest_json != _bundled_placeholder(existing.release_id):
            raise ReleaseError(f"release {manifest.release_id} is already stored with a different manifest")
        existing.manifest_json = canonical
        existing.manifest_sha256 = digest
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    release = Release(
        release_id=manifest.release_id,
        status=STATUS_STAGED,
        manifest_json=canonical,
        manifest_sha256=digest,
        staged_at=clock.now(),
    )
    session.add(release)
    session.commit()
    session.refresh(release)
    return release


def record_bundled_release(session: Session) -> None:
    """Record the hub's own release with a placeholder until its manifest is staged."""
    release_id = hub_release_id()
    if session.get(Release, release_id) is not None:
        return
    body = _bundled_placeholder(release_id)
    session.add(Release(
        release_id=release_id,
        status=STATUS_BUNDLED,
        manifest_json=body,
        manifest_sha256=hashlib.sha256(body.encode()).hexdigest(),
        staged_at=clock.now(),
    ))
    session.commit()


def is_known(session: Session, release_id: str) -> bool:
    """Staged or bundled: admissible for evaluation jobs and evaluation records."""
    return release_id == hub_release_id() or session.get(Release, release_id) is not None


def approved_tuples(session: Session, organization_id: str) -> set[tuple[str, str, str]]:
    """``(execution_profile, platform, release_id)`` approved for production in this organization."""
    rows = session.exec(
        select(ApprovedReleaseTuple).where(ApprovedReleaseTuple.organization_id == organization_id)
    ).all()
    return {(row.execution_profile, row.platform, row.release_id) for row in rows}


def may_run(
    *,
    worker_release: str | None,
    purpose: str,
    snapshot_release: str | None,
    execution_profile: str,
    candidate: Candidate,
    approved: set[tuple[str, str, str]],
) -> bool:
    """Whether a worker on ``worker_release`` may run this job with ``candidate``.

    Evaluation jobs run only on the release under test; production jobs need a
    promoted evaluation of ``(execution_profile, platform, worker_release)``.
    """
    if not worker_release:
        return False
    if purpose == "evaluation":
        return worker_release == snapshot_release
    return (execution_profile, platform_of(candidate), worker_release) in approved


def release_view(session: Session, release: Release, organization_id: str) -> dict:
    """What a worker of ``organization_id`` may learn about one release."""
    tuples = sorted(
        [p, pl] for (p, pl, rel) in approved_tuples(session, organization_id) if rel == release.release_id
    )
    return {
        "release_id": release.release_id,
        "status": release.status,
        "manifest_sha256": release.manifest_sha256,
        # The hub's own release; like any other it runs production jobs only for approved tuples.
        "bundled": release.release_id == hub_release_id(),
        "approved_tuples": [{"execution_profile": p, "platform": pl} for p, pl in tuples],
    }


__all__ = [
    "PLATFORMS",
    "Platform",
    "ReleaseError",
    "STATUS_BUNDLED",
    "STATUS_STAGED",
    "approved_tuples",
    "hub_release_id",
    "hub_version",
    "is_known",
    "may_run",
    "parse_manifest",
    "platform_of",
    "record_bundled_release",
    "release_view",
    "stage_release",
]
