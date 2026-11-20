"""Webhook endpoint registry, URL rules and the delivery outbox.

An organization registers the URLs the hub may notify when a job finishes; a
job later names one of them. :func:`validate_url` is the single gate for those
URLs: the routes apply it at registration and the dispatcher applies it again
before every send, because a host name can change its address in between.

The signing secret is stored in plaintext (HMAC needs it). It leaves this
module only through the create and rotate responses and is never logged.

:func:`enqueue_job_event` writes the outbox row for a job's terminal transition;
callers run it inside the transaction that makes the job terminal.
"""

from __future__ import annotations

import base64
import ipaddress
import re
import secrets
import socket
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from decouple import config
from sqlmodel import Session, select

from resona_job_core.webhooks import EVENT_OF_STATUS, SECRET_PREFIX, JobEvent

from . import clock
from .db.models import Job, JobStatus, WebhookDelivery, WebhookEndpoint

MAX_URL_CHARS = 2048
WEBHOOK_PURPOSE = "production"
"""Only client jobs notify: evaluation and testbench jobs never write deliveries."""
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f]")


class InvalidWebhookUrl(ValueError):
    """The URL breaks a webhook rule. The message never repeats the URL."""


def is_webhook_name(name: str) -> bool:
    return isinstance(name, str) and _NAME_RE.fullmatch(name) is not None


def new_secret() -> str:
    """``whsec_`` + 32 random bytes in base64 (the Standard Webhooks secret format)."""
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def allow_private() -> bool:
    """Hub operator switch: internal targets (and plain ``http`` to them) are allowed."""
    return config("RESONA_WEBHOOK_ALLOW_PRIVATE", default=False, cast=bool)


def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to. Raises ``OSError`` when it does not resolve."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_internal(address: str) -> bool:
    """Loopback, private, link-local, unspecified or otherwise not publicly routable."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or not ip.is_global
    )


def validate_url(url: str) -> None:
    """Raise :class:`InvalidWebhookUrl` unless the hub may send webhooks to ``url``.

    Rules: at most :data:`MAX_URL_CHARS` characters, no userinfo, scheme ``https``,
    and no address of the host may be loopback, private, link-local or unspecified.
    With ``RESONA_WEBHOOK_ALLOW_PRIVATE=true`` such internal hosts are accepted, and
    for them ``http`` as well; ``http`` to a public host is never accepted. A host
    name that does not resolve is rejected. Resolves the host (blocking).
    """
    if not isinstance(url, str) or not url:
        raise InvalidWebhookUrl("the URL is empty")
    if len(url) > MAX_URL_CHARS:
        raise InvalidWebhookUrl(f"the URL is longer than {MAX_URL_CHARS} characters")
    if _UNSAFE_CHARS.search(url):
        raise InvalidWebhookUrl("the URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # raises ValueError for a port outside 0-65535
    except ValueError:
        raise InvalidWebhookUrl("the URL cannot be parsed") from None
    if parts.scheme not in ("https", "http"):
        raise InvalidWebhookUrl("the URL scheme must be https")
    if "@" in parts.netloc:
        raise InvalidWebhookUrl("the URL must not contain user or password")
    if not host:
        raise InvalidWebhookUrl("the URL has no host")

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = _resolve(host)
        except (OSError, UnicodeError):
            addresses = []
        if not addresses:
            raise InvalidWebhookUrl("the URL host does not resolve") from None

    try:
        internal = any(_is_internal(address) for address in addresses)
    except ValueError:
        raise InvalidWebhookUrl("the URL host does not resolve to an IP address") from None
    allowed = allow_private()
    if internal and not allowed:
        raise InvalidWebhookUrl(
            "the URL host is a loopback, private or link-local address "
            "(the hub operator can allow these with RESONA_WEBHOOK_ALLOW_PRIVATE)"
        )
    if parts.scheme == "http" and not (internal and allowed):
        raise InvalidWebhookUrl("the URL scheme must be https")


# ── registry ────────────────────────────────────────────────────────────

def list_endpoints(session: Session, organization_id: str) -> list[WebhookEndpoint]:
    """The organization's unrevoked endpoints, by name."""
    return list(
        session.exec(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.organization_id == organization_id)
            .where(WebhookEndpoint.revoked_at.is_(None))
            .order_by(WebhookEndpoint.name)
        ).all()
    )


def get_endpoint(session: Session, organization_id: str, name: str) -> WebhookEndpoint | None:
    """The organization's unrevoked endpoint called ``name``, or None."""
    return session.exec(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == organization_id)
        .where(WebhookEndpoint.name == name)
        .where(WebhookEndpoint.revoked_at.is_(None))
    ).first()


def new_endpoint(organization_id: str, name: str, url: str) -> WebhookEndpoint:
    """An unsaved endpoint with a fresh id and secret; the caller validates and commits."""
    return WebhookEndpoint(
        id=f"wh_{secrets.token_hex(8)}",
        organization_id=organization_id,
        name=name,
        url=url,
        secret=new_secret(),
        created_at=clock.now(),
    )


def endpoint_names(session: Session, endpoint_ids: set[str]) -> dict[str, str]:
    """``id -> name`` of the given endpoints, revoked ones included (a job keeps the name it named)."""
    if not endpoint_ids:
        return {}
    rows = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.id.in_(sorted(endpoint_ids)))).all()
    return {row.id: row.name for row in rows}


# ── outbox ──────────────────────────────────────────────────────────────

def enqueue_job_event(session: Session, job: Job, now: datetime) -> WebhookDelivery | None:
    """Add the delivery row for ``job``'s terminal transition to the caller's transaction.

    Writes nothing for a job without an endpoint, for evaluation and testbench jobs,
    for a non-terminal status, and when the endpoint was revoked since admission.
    The payload is a :class:`~resona_job_core.webhooks.JobEvent`: identifiers only.
    """
    if job.webhook_endpoint_id is None or job.purpose != WEBHOOK_PURPOSE:
        return None
    status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
    event_type = EVENT_OF_STATUS.get(status)
    if event_type is None:
        return None
    endpoint = session.get(WebhookEndpoint, job.webhook_endpoint_id)
    if endpoint is None or endpoint.revoked_at is not None:
        return None
    event = JobEvent(
        type=event_type,
        job_id=job.id,
        kind=job.kind,
        status=status,
        error_code=None if status == JobStatus.COMPLETED.value else job.error_code,
        finished_at=now,
    )
    delivery = WebhookDelivery(
        id=uuid.uuid4().hex,
        organization_id=job.organization_id,
        endpoint_id=endpoint.id,
        job_id=job.id,
        event=event.type,
        payload_json=event.model_dump_json(),
        created_at=now,
        next_attempt_at=now,
    )
    session.add(delivery)
    return delivery
