"""Credential-based authentication for resona-api.

Every request resolves to a :class:`Principal` from a stored, unrevoked
credential. There is no auth-disabled mode. Tokens are accepted as
``Authorization: Bearer <token>`` or ``X-API-Key: <token>``; when both are sent
they must match. Only the SHA-256 hash of a token is stored.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from .db.engine import engine
from .db.models import Credential, Worker

logger = logging.getLogger(__name__)

ROLE_CLIENT = "client"
ROLE_ORG_ADMIN = "org_admin"
ROLE_WORKER = "worker"
ROLE_HUB_OPERATOR = "hub_operator"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``organization_id`` is server-derived, never requested."""

    credential_id: str
    organization_id: str
    role: str
    worker_id: Optional[str]


def hash_token(token: str) -> str:
    """SHA-256 hex digest of an issued token; the only form stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_token(session: Session, token: str | None) -> Principal | None:
    """Return the principal for an active credential, or None.

    Revoked credentials, and worker credentials whose worker is revoked, do not resolve.
    """
    if not token:
        return None
    cred = session.exec(
        select(Credential).where(Credential.secret_hash == hash_token(token))
    ).first()
    if cred is None or cred.revoked_at is not None:
        return None
    if cred.role == ROLE_WORKER:
        worker = session.get(Worker, cred.worker_id) if cred.worker_id else None
        if worker is None or worker.revoked_at is not None:
            return None
    return Principal(
        credential_id=cred.id,
        organization_id=cred.organization_id,
        role=cred.role,
        worker_id=cred.worker_id,
    )


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "unauthorized", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "forbidden", "message": message},
    )


def _db_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Authorization must be 'Bearer <token>'")
    return token.strip()


async def get_principal(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    session: Session = Depends(_db_session),
) -> Principal:
    """Authenticate the request from its credential headers."""
    bearer = _bearer_token(authorization)
    api_key = x_api_key.strip() if x_api_key else None
    if bearer is not None and api_key is not None and bearer != api_key:
        raise _unauthorized("Conflicting credentials in Authorization and X-API-Key")
    token = bearer if bearer is not None else api_key
    if token is None:
        raise _unauthorized("Missing credentials")
    principal = resolve_token(session, token)
    if principal is None:
        logger.warning("Rejected request with unknown or revoked credential")
        raise _unauthorized("Invalid or revoked credential")
    return principal


def require_roles(*roles: str) -> Callable[..., Principal]:
    """Dependency factory: authenticated principal whose role is one of ``roles``."""
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            raise _forbidden("Credential role is not permitted for this operation")
        return principal

    dependency.__name__ = f"require_{'_'.join(sorted(allowed))}"
    return dependency


client_principal = require_roles(ROLE_CLIENT, ROLE_ORG_ADMIN)
admin_principal = require_roles(ROLE_ORG_ADMIN)
worker_principal = require_roles(ROLE_WORKER)
# Admin API only: an org_admin manages its own organization, a hub_operator every
# organization. The operator is deliberately absent from client/admin principals, so it
# never submits jobs, reads results or changes settings through the other routes.
manager_principal = require_roles(ROLE_ORG_ADMIN, ROLE_HUB_OPERATOR)


def _ws_candidates(websocket) -> list[str]:
    """Every credential the WebSocket handshake presents, in header-then-query order."""
    found: list[str] = []
    authorization = websocket.headers.get("authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() in ("bearer", "token") and token.strip():
            found.append(token.strip())
        else:
            found.append("")  # malformed header counts as a presented, invalid credential
    api_key = websocket.headers.get("x-api-key")
    if api_key:
        found.append(api_key.strip())
    for name in ("token", "api_key"):
        value = websocket.query_params.get(name)
        if value:
            found.append(value.strip())
    return found


def websocket_principal(websocket, session: Session) -> Principal | None:
    """Resolve a WebSocket handshake to a principal; None when absent, invalid or conflicting."""
    presented = _ws_candidates(websocket)
    if not presented or len(set(presented)) != 1:
        return None
    return resolve_token(session, presented[0])
