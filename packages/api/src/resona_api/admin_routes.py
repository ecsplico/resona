"""Hub management API (``/v1/admin``) and the static management page (``/admin``).

An ``org_admin`` manages only its own organization; a ``hub_operator`` manages every
organization. Organization-scoped lookups answer 404 for organizations outside the
caller's reach, so foreign ids are indistinguishable from missing ones.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import clock
from .admin import (
    AdminError,
    approve_worker,
    create_organization,
    issue_credential,
    revoke_credential,
    revoke_worker,
)
from .auth import ROLE_CLIENT, ROLE_HUB_OPERATOR, ROLE_ORG_ADMIN, ROLE_WORKER, Principal, manager_principal
from .db.models import ApprovedReleaseTuple, Baseline, Credential, Evaluation, Organization, Worker
from .deps import get_db_session
from .errors import api_error
from .jobs_routes import DEFAULT_PAGE_SIZE, WORKER_ONLINE_WINDOW, jobs_page
from .settings_store import current_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["Admin"])
static_router = APIRouter(include_in_schema=False)

STATIC_DIR = Path(__file__).parent / "static" / "admin"
_ASSETS = {
    "admin.js": "text/javascript; charset=utf-8",
    "admin.css": "text/css; charset=utf-8",
}
_STATIC_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data: blob:; media-src blob:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
VENDOR_DIR = STATIC_DIR / "vendor" / "webawesome"
ICONS_DIR = STATIC_DIR / "icons"
_VENDOR_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
_ICON_NAME = re.compile(r"^[a-z0-9-]+\.svg$")
_CACHED_HEADERS = {**_STATIC_HEADERS, "Cache-Control": "public, max-age=86400"}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return clock.as_utc(value).isoformat() if value is not None else None


def _target_org(session: Session, principal: Principal, org_id: str) -> Organization:
    """The organization ``org_id`` if the caller may manage it, else 404.

    An org_admin reaches only its own organization; a hub_operator any existing one.
    """
    not_found = api_error(404, "organization_not_found", "no such organization")
    if principal.role != ROLE_HUB_OPERATOR and org_id != principal.organization_id:
        raise not_found
    org = session.get(Organization, org_id)
    if org is None:
        raise not_found
    return org


def _conflict(exc: AdminError) -> HTTPException:
    """An invalid admin operation reported by :mod:`resona_api.admin`."""
    return api_error(409, "conflict", str(exc))


def _organization_item(org: Organization) -> dict[str, Any]:
    return {"id": org.id, "created_at": _iso(org.created_at)}


class OrganizationCreate(BaseModel):
    id: str = Field(min_length=1)


@router.get("/me")
def get_me(principal: Principal = Depends(manager_principal)) -> dict[str, Any]:
    """The caller's role and home organization."""
    return {
        "role": principal.role,
        "organization_id": principal.organization_id,
        "credential_id": principal.credential_id,
    }


@router.get("/organizations")
def list_organizations(
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Every organization for a hub operator; only its own for an org admin."""
    if principal.role == ROLE_HUB_OPERATOR:
        orgs = list(session.exec(select(Organization).order_by(Organization.id)).all())
    else:
        orgs = [_target_org(session, principal, principal.organization_id)]
    return {"items": [_organization_item(org) for org in orgs]}


@router.post("/organizations", status_code=201)
def post_organization(
    body: OrganizationCreate,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create an organization with default settings (hub operators only)."""
    if principal.role != ROLE_HUB_OPERATOR:
        raise api_error(403, "forbidden", "Only a hub operator can create organizations")
    try:
        org = create_organization(session, body.id)
    except AdminError as exc:
        raise _conflict(exc) from exc
    logger.info("created organization %s", org.id)
    return _organization_item(org)


def _capabilities(raw: Optional[str]) -> list[dict[str, Any]]:
    return json.loads(raw or "[]")


def _worker_item(worker: Worker, now: datetime) -> dict[str, Any]:
    revoked = worker.revoked_at is not None
    online = (
        not revoked
        and worker.last_seen is not None
        and clock.as_utc(worker.last_seen) >= now - WORKER_ONLINE_WINDOW
    )
    return {
        "id": worker.id,
        "name": worker.name,
        "approved": worker.approved,
        "approved_private": worker.approved_private,
        "online": online,
        "last_seen": _iso(worker.last_seen),
        "runtime_version": worker.runtime_version,
        "revoked": revoked,
        "reported_capabilities": _capabilities(worker.reported_capabilities),
        "approved_capabilities": _capabilities(worker.approved_capabilities),
    }


def _org_worker(session: Session, org: Organization, worker_id: str) -> Worker:
    """The organization's worker ``worker_id``; another organization's worker is 404 too."""
    worker = session.get(Worker, worker_id)
    if worker is None or worker.organization_id != org.id:
        raise api_error(404, "worker_not_found", "no such worker")
    return worker


@router.get("/organizations/{org_id}/workers")
def list_workers(
    org_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's workers: active ones first, then by name."""
    org = _target_org(session, principal, org_id)
    workers = session.exec(select(Worker).where(Worker.organization_id == org.id)).all()
    now = clock.now()
    ordered = sorted(workers, key=lambda w: (w.revoked_at is not None, w.name, w.id))
    return {"items": [_worker_item(worker, now) for worker in ordered]}


class WorkerApproval(BaseModel):
    private: bool


@router.post("/organizations/{org_id}/workers/{worker_id}/approve")
def post_worker_approval(
    org_id: str,
    worker_id: str,
    body: WorkerApproval,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Approve a worker with exactly the capabilities it last reported."""
    org = _target_org(session, principal, org_id)
    worker = _org_worker(session, org, worker_id)
    reported = _capabilities(worker.reported_capabilities)
    if not reported:
        raise api_error(
            409,
            "no_reported_capabilities",
            "the worker has not reported capabilities; run it once after `resona-worker prepare`",
        )
    try:
        worker = approve_worker(session, worker.id, private=body.private, capabilities=reported)
    except AdminError as exc:
        raise _conflict(exc) from exc
    logger.info("approved worker %s private %s", worker.id, body.private)
    return _worker_item(worker, clock.now())


@router.post("/organizations/{org_id}/workers/{worker_id}/revoke")
def post_worker_revocation(
    org_id: str,
    worker_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Revoke a worker and its credential; revoking again changes nothing."""
    org = _target_org(session, principal, org_id)
    worker = _org_worker(session, org, worker_id)
    try:
        revoke_worker(session, worker.id)
    except AdminError as exc:
        raise _conflict(exc) from exc
    session.refresh(worker)
    logger.info("revoked worker %s", worker.id)
    return _worker_item(worker, clock.now())


def _credential_item(cred: Credential) -> dict[str, Any]:
    return {
        "id": cred.id,
        "role": cred.role,
        "worker_id": cred.worker_id,
        "created_at": _iso(cred.created_at),
        "revoked_at": _iso(cred.revoked_at),
    }


@router.get("/organizations/{org_id}/credentials")
def list_credentials(
    org_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's credentials, newest first; never their secret hashes."""
    org = _target_org(session, principal, org_id)
    creds = session.exec(
        select(Credential)
        .where(Credential.organization_id == org.id)
        .order_by(Credential.created_at.desc(), Credential.id.desc())
    ).all()
    return {"items": [_credential_item(cred) for cred in creds]}


class CredentialIssue(BaseModel):
    role: Literal["client", "org_admin"]


@router.post("/organizations/{org_id}/credentials", status_code=201)
def post_credential(
    org_id: str,
    body: CredentialIssue,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Issue a client or org_admin token; the token appears only in this response."""
    org = _target_org(session, principal, org_id)
    role = ROLE_CLIENT if body.role == "client" else ROLE_ORG_ADMIN
    try:
        cred_id, token = issue_credential(session, org.id, role)
    except AdminError as exc:
        raise _conflict(exc) from exc
    logger.info("issued credential %s role %s", cred_id, role)
    return {"id": cred_id, "role": role, "token": token}


@router.post("/organizations/{org_id}/credentials/{cred_id}/revoke")
def post_credential_revocation(
    org_id: str,
    cred_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Revoke a client or org_admin credential; worker credentials go through worker revoke."""
    org = _target_org(session, principal, org_id)
    cred = session.get(Credential, cred_id)
    if cred is None or cred.organization_id != org.id:
        raise api_error(404, "credential_not_found", "no such credential")
    if cred.id == principal.credential_id:
        raise api_error(409, "cannot_revoke_self", "you cannot revoke the credential you are signed in with")
    if cred.role == ROLE_WORKER:
        raise api_error(409, "use_worker_revoke", "revoke the worker instead of its credential")
    try:
        revoke_credential(session, cred.id)
    except AdminError as exc:
        raise _conflict(exc) from exc
    session.refresh(cred)
    logger.info("revoked credential %s", cred.id)
    return _credential_item(cred)


@router.get("/organizations/{org_id}/jobs")
def list_org_jobs(
    org_id: str,
    cursor: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """One page of the organization's job metadata, same shape as ``GET /v1/jobs``."""
    org = _target_org(session, principal, org_id)
    return jobs_page(session, org.id, cursor, limit)


@router.get("/organizations/{org_id}/settings")
def get_org_settings(
    org_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's current settings revision (read-only)."""
    org = _target_org(session, principal, org_id)
    try:
        revision, settings = current_settings(session, org.id)
    except LookupError as exc:
        raise api_error(404, "settings_not_found", "the organization has no settings") from exc
    return {"revision": revision, "settings": settings.model_dump(mode="json")}


@router.get("/organizations/{org_id}/baselines")
def list_baselines(
    org_id: str,
    principal: Principal = Depends(manager_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The current baseline per (execution profile, platform) and the approved releases."""
    org = _target_org(session, principal, org_id)
    rows = session.exec(
        select(Baseline, Evaluation)
        .join(Evaluation, Evaluation.id == Baseline.evaluation_id)
        .where(Baseline.organization_id == org.id)
        .order_by(Baseline.execution_profile, Baseline.platform, Baseline.revision.desc())
    ).all()
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for baseline, evaluation in rows:
        key = (baseline.execution_profile, baseline.platform)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "execution_profile": baseline.execution_profile,
            "platform": baseline.platform,
            "revision": baseline.revision,
            "evaluation_id": baseline.evaluation_id,
            "release_id": evaluation.release_id,
            "corpus_sha256": evaluation.corpus_sha256,
            "created_at": _iso(baseline.created_at),
        })
    approved = session.exec(
        select(ApprovedReleaseTuple)
        .where(ApprovedReleaseTuple.organization_id == org.id)
        .order_by(
            ApprovedReleaseTuple.execution_profile,
            ApprovedReleaseTuple.platform,
            ApprovedReleaseTuple.approved_at,
        )
    ).all()
    return {
        "items": items,
        "approved_releases": [
            {
                "execution_profile": row.execution_profile,
                "platform": row.platform,
                "release_id": row.release_id,
                "evaluation_id": row.evaluation_id,
                "approved_at": _iso(row.approved_at),
            }
            for row in approved
        ],
    }


def _static(path: Path, media_type: str) -> FileResponse:
    return FileResponse(path, media_type=media_type, headers=_STATIC_HEADERS)


def _contained_file(base: Path, relative: str) -> Path:
    """``relative`` resolved under ``base``; 404 unless it is a regular file inside it."""
    not_found = api_error(404, "not_found", "no such asset")
    root = base.resolve()
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError) as exc:
        raise not_found from exc
    if not target.is_relative_to(root) or not target.is_file():
        raise not_found
    return target


@static_router.get("/admin")
def admin_page() -> FileResponse:
    """The management page; it signs in with a token and calls ``/v1/admin``."""
    return _static(STATIC_DIR / "index.html", "text/html; charset=utf-8")


@static_router.get("/admin/vendor/webawesome/{path:path}")
def admin_vendor(path: str) -> FileResponse:
    """The vendored Web Awesome component library (scripts and styles only)."""
    target = _contained_file(VENDOR_DIR, path)
    media_type = _VENDOR_TYPES.get(target.suffix)
    if media_type is None:
        raise api_error(404, "not_found", "no such asset")
    return FileResponse(target, media_type=media_type, headers=_CACHED_HEADERS)


@static_router.get("/admin/icons/{name}")
def admin_icon(name: str) -> FileResponse:
    """A local SVG icon used by the management page."""
    if not _ICON_NAME.fullmatch(name):
        raise api_error(404, "not_found", "no such icon")
    return FileResponse(_contained_file(ICONS_DIR, name), media_type="image/svg+xml", headers=_CACHED_HEADERS)


@static_router.get("/admin/{asset}")
def admin_asset(asset: str) -> FileResponse:
    media_type = _ASSETS.get(asset)
    if media_type is None:
        raise api_error(404, "not_found", "no such asset")
    return _static(STATIC_DIR / asset, media_type)
