"""Organization-scoped postprocessing profiles: revisioned, admin-written, name-addressed.

A stored profile may include others (``{"type": "include", "profile": ...}``): writes keep
every include resolvable, and jobs pin the flattened document (``privacy.resolve_includes``).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from resona_postprocess.profile import Profile, ProfileError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import schemas
from .auth import Principal, admin_principal, client_principal
from .db.models import ProfileRevision
from .deps import get_db_session
from .openapi import CLIENT
from .privacy import (
    BUNDLED_PROFILE_NAME,
    ProfileNotFound,
    dependents,
    is_profile_name,
    load_org_profile,
    resolve_includes,
    validate_profile_body,
)

router = APIRouter(tags=[CLIENT])


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


@router.get(
    "/profiles", summary="List the organization's profiles", tags=["Config"], operation_id="profiles_list",
    response_model=schemas.ProfileList, responses=schemas.error_responses(),
)
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


@router.get(
    "/profiles/{name}", summary="Get one profile", tags=["Config"], operation_id="profiles_get",
    response_model=schemas.ProfileView, response_model_exclude_unset=True,
    responses=schemas.error_responses({
        404: [schemas.DETAIL], 409: ["stored_profile_invalid"], 422: ["invalid_profile_name"],
    }),
)
def get_profile_route(
    name: str,
    resolved: bool = Query(False, description="Return the flattened profile: include steps replaced by "
                                                "the steps of the profiles they name, plus `includes`."),
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
):
    """Return the current revision of a profile for the caller's organization."""
    _require_name(name)
    try:
        profile = load_org_profile(session, principal.organization_id, name)
        if resolved:
            profile, includes = resolve_includes(session, principal.organization_id, profile)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="profile not found")
    except ProfileError:
        raise HTTPException(status_code=409, detail={"error": "stored_profile_invalid"})
    row = _latest_row(session, principal.organization_id, name)
    view = {
        "name": name,
        "revision": row.revision if row is not None else 0,
        "profile": profile.to_dict(),
    }
    if resolved:
        view["includes"] = includes
    return view


def _invalid_profile(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "invalid_profile", "message": message},
    )


def _check_includes(session: Session, organization_id: str, name: str, profile: Profile) -> None:
    """Refuse a write that leaves ``profile`` or a stored profile including it unresolvable.

    A dependent that did not resolve before this write is not this write's doing and
    does not block it.
    """
    try:
        resolve_includes(session, organization_id, profile, overrides={name: profile})
    except ProfileError as exc:
        raise _invalid_profile(str(exc)) from None
    for dependent in dependents(session, organization_id, name):
        try:
            current = load_org_profile(session, organization_id, dependent)
            resolve_includes(session, organization_id, current)
        except (ProfileNotFound, ProfileError):
            continue
        try:
            resolve_includes(session, organization_id, current, overrides={name: profile})
        except ProfileError as exc:
            raise _invalid_profile(f"profile {dependent!r} would no longer resolve: {exc}") from None


@router.put(
    "/profiles/{name}", summary="Create the next revision of a profile", tags=["Config"],
    operation_id="profiles_put", response_model=schemas.ProfileRef,
    responses=schemas.error_responses({
        409: ["reserved_profile_name", "revision_conflict"],
        422: ["invalid_profile_name", "invalid_profile", schemas.VALIDATION],
    }),
)
def put_profile_route(
    name: str,
    body: dict,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Store a new revision (organization admin only). Inline rules only; no file sources.

    Every include must resolve (stored profile, else bundled block) without a cycle, and
    stored profiles that include this one must still resolve with the new revision.
    """
    _require_name(name)
    if name == BUNDLED_PROFILE_NAME:
        raise HTTPException(status_code=409, detail={"error": "reserved_profile_name"})
    try:
        profile = validate_profile_body(body)
    except ProfileError as exc:
        raise _invalid_profile(str(exc)) from None
    _check_includes(session, principal.organization_id, name, profile)
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


@router.delete(
    "/profiles/{name}", summary="Delete a profile", tags=["Config"], operation_id="profiles_delete",
    response_model=schemas.Deleted,
    responses=schemas.error_responses({
        404: [schemas.DETAIL], 409: ["profile_in_use", "revision_conflict"], 422: ["invalid_profile_name"],
    }),
)
def delete_profile_route(
    name: str,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Tombstone a profile (organization admin only); earlier revisions stay for provenance.

    A profile another stored profile includes, directly or indirectly, stays (409 ``profile_in_use``).
    """
    _require_name(name)
    latest = _latest_row(session, principal.organization_id, name)
    if latest is None or latest.deleted:
        raise HTTPException(status_code=404, detail="profile not found")
    users = dependents(session, principal.organization_id, name)
    if users:
        raise HTTPException(status_code=409, detail={
            "error": "profile_in_use", "message": f"included by {', '.join(users)}",
        })
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
