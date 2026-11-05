"""Hub management API (``/v1/admin``) and the static management page (``/admin``).

An ``org_admin`` manages only its own organization; a ``hub_operator`` manages every
organization. Organization-scoped lookups answer 404 for organizations outside the
caller's reach, so foreign ids are indistinguishable from missing ones.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import clock
from .admin import AdminError, create_organization
from .auth import ROLE_HUB_OPERATOR, Principal, manager_principal
from .db.models import Organization
from .deps import get_db_session
from .errors import api_error

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
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


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


def _static(path: Path, media_type: str) -> FileResponse:
    return FileResponse(path, media_type=media_type, headers=_STATIC_HEADERS)


@static_router.get("/admin")
def admin_page() -> FileResponse:
    """The management page; it signs in with a token and calls ``/v1/admin``."""
    return _static(STATIC_DIR / "index.html", "text/html; charset=utf-8")


@static_router.get("/admin/{asset}")
def admin_asset(asset: str) -> FileResponse:
    media_type = _ASSETS.get(asset)
    if media_type is None:
        raise api_error(404, "not_found", "no such asset")
    return _static(STATIC_DIR / asset, media_type)
