"""Organization-scoped postprocessing profiles: revisioned, admin-written, name-addressed."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from resona_postprocess.profile import ProfileError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .auth import Principal, admin_principal, client_principal
from .db.models import ProfileRevision
from .deps import get_db_session
from .privacy import (
    BUNDLED_PROFILE_NAME,
    ProfileNotFound,
    is_profile_name,
    load_org_profile,
    validate_profile_body,
)

router = APIRouter()


def _require_name(name: str) -> None:
    if not is_profile_name(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_profile_name"},
        )


def _latest_row(session: Session, organization_id: str, name: str) -> ProfileRevision | None:
    return session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id)
        .where(ProfileRevision.name == name)
        .order_by(ProfileRevision.revision.desc())
    ).first()


@router.get("/profiles", summary="List the organization's profiles", tags=["Config"])
def list_profiles_route(
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
):
    """Live profile names for the caller's organization, including the bundled default."""
    stored = session.exec(
        select(ProfileRevision.name)
        .where(ProfileRevision.organization_id == principal.organization_id)
        .distinct()
    ).all()
    profiles = []
    for name in sorted(set(stored) | {BUNDLED_PROFILE_NAME}):
        try:
            load_org_profile(session, principal.organization_id, name)
        except (ProfileNotFound, ProfileError):
            continue
        row = _latest_row(session, principal.organization_id, name)
        profiles.append({"name": name, "revision": row.revision if row is not None else 0})
    return {"profiles": profiles}


@router.get("/profiles/{name}", summary="Get one profile", tags=["Config"])
def get_profile_route(
    name: str,
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
):
    """Return the current revision of a profile for the caller's organization."""
    _require_name(name)
    try:
        profile = load_org_profile(session, principal.organization_id, name)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="profile not found")
    except ProfileError:
        raise HTTPException(status_code=409, detail={"error": "stored_profile_invalid"})
    row = _latest_row(session, principal.organization_id, name)
    return {
        "name": name,
        "revision": row.revision if row is not None else 0,
        "profile": profile.to_dict(),
    }


@router.put("/profiles/{name}", summary="Create the next revision of a profile", tags=["Config"])
def put_profile_route(
    name: str,
    body: dict,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Store a new revision (organization admin only). Inline rules only; no file sources."""
    _require_name(name)
    if name == BUNDLED_PROFILE_NAME:
        raise HTTPException(status_code=409, detail={"error": "reserved_profile_name"})
    try:
        validate_profile_body(body)
    except ProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_profile", "message": str(exc)},
        )
    latest = _latest_row(session, principal.organization_id, name)
    revision = (latest.revision if latest is not None else 0) + 1
    session.add(
        ProfileRevision(
            organization_id=principal.organization_id,
            name=name,
            revision=revision,
            profile_json=json.dumps(body),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail={"error": "revision_conflict"})
    return {"name": name, "revision": revision}


@router.delete("/profiles/{name}", summary="Delete a profile", tags=["Config"])
def delete_profile_route(
    name: str,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Tombstone a profile (organization admin only); earlier revisions stay for provenance."""
    _require_name(name)
    latest = _latest_row(session, principal.organization_id, name)
    if latest is None or latest.deleted:
        raise HTTPException(status_code=404, detail="profile not found")
    session.add(
        ProfileRevision(
            organization_id=principal.organization_id,
            name=name,
            revision=latest.revision + 1,
            profile_json="{}",
            deleted=True,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail={"error": "revision_conflict"})
    return {"ok": True}
