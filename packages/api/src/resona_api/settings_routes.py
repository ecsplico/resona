"""Organization settings: revisioned read (any client) and admin-only replace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from resona_job_core import OrganizationSettings
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from .auth import Principal, admin_principal, client_principal
from .db.models import SettingsRevision
from .deps import get_db_session
from .settings_store import current_settings

router = APIRouter()


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _parse_if_match(value: str) -> int | None:
    """Return the revision named by an ``If-Match`` value, or None when malformed."""
    candidate = value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:]
    candidate = candidate.strip('"')
    return int(candidate) if candidate.isdigit() else None


@router.get("/v1/settings", tags=["Settings"])
def get_settings(
    response: Response,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
):
    """The organization's current settings revision (secrets are never stored here)."""
    try:
        revision, settings = current_settings(session, principal.organization_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Settings not found")
    response.headers["ETag"] = _etag(revision)
    return {
        "organization_id": principal.organization_id,
        "revision": revision,
        "settings": settings.model_dump(mode="json"),
    }


@router.put("/v1/settings", tags=["Settings"])
def put_settings(
    body: OrganizationSettings,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Replace the organization's settings; creates the next revision.

    ``If-Match`` must name the current revision: missing → 428, stale → 409.
    """
    if if_match is None:
        raise HTTPException(status_code=428, detail="If-Match header is required")
    expected = _parse_if_match(if_match)
    if expected is None:
        raise HTTPException(status_code=400, detail="If-Match must name a revision")
    try:
        current, _ = current_settings(session, principal.organization_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Settings not found")
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail={"error": "revision_conflict", "current_revision": current},
        )

    new_revision = current + 1
    session.add(
        SettingsRevision(
            organization_id=principal.organization_id,
            revision=new_revision,
            settings_json=body.model_dump_json(),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail={"error": "revision_conflict"})

    response.headers["ETag"] = _etag(new_revision)
    return {
        "organization_id": principal.organization_id,
        "revision": new_revision,
        "settings": body.model_dump(mode="json"),
    }
