"""Organization webhook endpoints (``/v1/webhooks``, role ``org_admin`` only).

The signing secret appears in the create and rotate responses and nowhere else.
Endpoints are addressed by name within the caller's organization, so another
organization's name is simply not found.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from . import clock, webhooks
from .auth import Principal, admin_principal
from .db.models import WebhookEndpoint
from .deps import get_db_session
from .errors import api_error
from .openapi import ADMIN

router = APIRouter(prefix="/v1/webhooks", tags=[ADMIN, "Webhooks"])


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


class WebhookView(BaseModel):
    name: str
    url: str
    created_at: datetime


class WebhookWithSecret(WebhookView):
    """Create and rotate only: ``secret`` is shown once and cannot be read again."""

    secret: str


class WebhookList(BaseModel):
    items: list[WebhookView]


class DeliveryView(BaseModel):
    """Delivery metadata. Never the payload body, the URL or the secret."""

    id: str
    job_id: int
    event: str
    attempts: int
    last_status: int | None
    delivered_at: datetime | None
    last_error: str | None


class DeliveryList(BaseModel):
    items: list[DeliveryView]


def _view(row: WebhookEndpoint) -> WebhookView:
    return WebhookView(name=row.name, url=row.url, created_at=clock.as_utc(row.created_at))


def _with_secret(row: WebhookEndpoint) -> WebhookWithSecret:
    return WebhookWithSecret(**_view(row).model_dump(), secret=row.secret)


def _found(session: Session, principal: Principal, name: str) -> WebhookEndpoint:
    row = webhooks.get_endpoint(session, principal.organization_id, name)
    if row is None:
        raise api_error(404, "webhook_not_found", "No webhook endpoint with this name")
    return row


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WebhookWithSecret,
    summary="Register a webhook endpoint",
)
def create_webhook_route(
    body: WebhookCreate,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Register a URL for job notifications. The response carries the signing secret once."""
    if not webhooks.is_webhook_name(body.name):
        raise api_error(
            422, "invalid_webhook_name",
            "name must be 1-63 characters of a-z, 0-9 and '-', not starting with '-'",
        )
    try:
        webhooks.validate_url(body.url)
    except webhooks.InvalidWebhookUrl as exc:
        raise api_error(422, "invalid_webhook_url", str(exc)) from None
    exists = api_error(409, "webhook_exists", "A webhook endpoint with this name already exists")
    if webhooks.get_endpoint(session, principal.organization_id, body.name) is not None:
        raise exists
    row = webhooks.new_endpoint(principal.organization_id, body.name, body.url)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:  # a concurrent create took the name
        session.rollback()
        raise exists from None
    return _with_secret(row)


@router.get("", response_model=WebhookList, summary="List webhook endpoints")
def list_webhooks_route(
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """The organization's unrevoked endpoints by name. Never contains secrets."""
    return WebhookList(items=[_view(row) for row in webhooks.list_endpoints(session, principal.organization_id)])


@router.get("/{name}", response_model=WebhookView, summary="Get one webhook endpoint")
def get_webhook_route(
    name: str,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """One unrevoked endpoint, without its secret."""
    return _view(_found(session, principal, name))


@router.get("/{name}/deliveries", response_model=DeliveryList, summary="List recent webhook deliveries")
def list_deliveries_route(
    name: str,
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """The endpoint's last ``limit`` deliveries, newest first: metadata only.

    ``last_error`` is ``expired`` (unsent 24 h after creation) or ``endpoint_revoked``
    for abandoned rows, else the last failed attempt's reason (``http_<status>``,
    ``timeout``, ``connection_error``, ``invalid_url``). Rows are kept seven days.
    """
    endpoint = _found(session, principal, name)
    return DeliveryList(items=[
        DeliveryView(
            id=row.id,
            job_id=row.job_id,
            event=row.event,
            attempts=row.attempts,
            last_status=row.last_status,
            delivered_at=clock.as_utc(row.delivered_at) if row.delivered_at is not None else None,
            last_error=row.last_error,
        )
        for row in webhooks.recent_deliveries(session, endpoint.id, limit)
    ])


@router.post("/{name}/rotate", response_model=WebhookWithSecret, summary="Rotate a webhook signing secret")
def rotate_webhook_route(
    name: str,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Replace the signing secret; the old one stops signing at once. The new one is shown once."""
    row = _found(session, principal, name)
    row.secret = webhooks.new_secret()
    session.add(row)
    session.commit()
    return _with_secret(row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a webhook endpoint")
def delete_webhook_route(
    name: str,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
):
    """Revoke the endpoint. The name becomes free for a new registration."""
    row = _found(session, principal, name)
    row.revoked_at = clock.now()
    session.add(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
