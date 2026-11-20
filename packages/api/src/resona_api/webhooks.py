"""Webhook endpoint registry, URL rules and the delivery outbox.

An organization registers the URLs the hub may notify when a job finishes; a
job later names one of them. :func:`validate_url` is the single gate for those
URLs: the routes apply it at registration and the dispatcher applies it again
before every send, because a host name can change its address in between.

The signing secret is stored in plaintext (HMAC needs it). It leaves this
module only through the create and rotate responses and is never logged.

:func:`enqueue_job_event` writes the outbox row for a job's terminal transition;
callers run it inside the transaction that makes the job terminal.
:func:`dispatch_loop` (a lifespan task beside the maintenance loop) sends those
rows: Standard Webhooks headers, retries by :data:`RETRY_DELAYS` then hourly,
abandonment 24 h after creation or when the endpoint is revoked, one delivery at
a time per endpoint in creation order, endpoints concurrently.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import re
import secrets
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from decouple import config
from sqlalchemy import func, literal_column, or_
from sqlmodel import Session, select

from resona_job_core.webhooks import EVENT_OF_STATUS, SECRET_PREFIX, JobEvent, sign

from . import clock, releases
from .db import engine as db_engine
from .db.models import Job, JobStatus, WebhookDelivery, WebhookEndpoint

log = logging.getLogger(__name__)

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


def recent_deliveries(session: Session, endpoint_id: str, limit: int) -> list[WebhookDelivery]:
    """The endpoint's last ``limit`` delivery rows, newest first."""
    return list(
        session.exec(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .order_by(WebhookDelivery.created_at.desc(), _ROWID.desc())
            .limit(limit)
        ).all()
    )


def delete_old_deliveries(session: Session, now: datetime) -> int:
    """Delete delivery rows created :data:`DELIVERY_RETENTION` or longer before ``now``; commit."""
    cutoff = now - DELIVERY_RETENTION
    rows = session.exec(select(WebhookDelivery).where(WebhookDelivery.created_at <= cutoff)).all()
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)


# ── dispatcher ──────────────────────────────────────────────────────────

RETRY_DELAYS = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
)
"""Wait after the 1st … 5th failed attempt; every later failure waits :data:`RETRY_INTERVAL`."""
RETRY_INTERVAL = timedelta(hours=1)
DELIVERY_TTL = timedelta(hours=24)
"""A delivery still unsent this long after its creation is abandoned (``last_error`` ``expired``)."""
DELIVERY_RETENTION = timedelta(days=7)
"""Maintenance deletes delivery rows (any state) this long after their creation."""
SEND_TIMEOUT_SECONDS = 10.0
DISPATCH_INTERVAL_SECONDS = 1.0
DISPATCH_BATCH = 100
"""At most this many endpoints start sending per pass."""
EXPIRED = "expired"
ENDPOINT_REVOKED = "endpoint_revoked"
_ABANDONED = (EXPIRED, ENDPOINT_REVOKED)
_ROWID = literal_column("webhookdelivery.rowid")
"""Tie-break for rows created in the same instant: insertion order."""


class _RedactRequestUrls(logging.Filter):
    """httpx logs every request URL at INFO; keep only its part before the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                str(arg).split("?", 1)[0] if isinstance(arg, httpx.URL) else arg for arg in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_RedactRequestUrls())


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next attempt once ``attempts`` (≥ 1) attempts have failed."""
    return RETRY_DELAYS[attempts - 1] if attempts <= len(RETRY_DELAYS) else RETRY_INTERVAL


def _pending():
    """Not delivered and not abandoned."""
    return (
        WebhookDelivery.delivered_at.is_(None),
        or_(WebhookDelivery.last_error.is_(None), WebhookDelivery.last_error.not_in(_ABANDONED)),
    )


def _abandon_reason(row: WebhookDelivery, endpoint: WebhookEndpoint, now: datetime) -> str | None:
    if endpoint.revoked_at is not None:
        return ENDPOINT_REVOKED
    if clock.as_utc(row.created_at) <= now - DELIVERY_TTL:
        return EXPIRED
    return None


def _abandon(session: Session, row: WebhookDelivery, endpoint: WebhookEndpoint, reason: str) -> None:
    row.last_error = reason
    session.add(row)
    log.info("Webhook %s delivery %s: abandoned (%s)", endpoint.name, row.id, reason)


def abandon_stale(session: Session, now: datetime) -> int:
    """Abandon pending rows of revoked endpoints and rows older than :data:`DELIVERY_TTL`; commit."""
    rows = session.exec(
        select(WebhookDelivery, WebhookEndpoint)
        .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
        .where(*_pending())
        .where(or_(WebhookEndpoint.revoked_at.is_not(None), WebhookDelivery.created_at <= now - DELIVERY_TTL))
    ).all()
    for row, endpoint in rows:
        _abandon(session, row, endpoint, _abandon_reason(row, endpoint, now) or EXPIRED)
    session.commit()
    return len(rows)


def due_endpoints(session: Session, now: datetime, busy: set[str], limit: int = DISPATCH_BATCH) -> list[str]:
    """Endpoints (not in ``busy``) with a pending row due at ``now``, longest-waiting first."""
    stmt = (
        select(WebhookDelivery.endpoint_id)
        .where(*_pending())
        .where(WebhookDelivery.next_attempt_at <= now)
        .group_by(WebhookDelivery.endpoint_id)
        .order_by(func.min(WebhookDelivery.next_attempt_at))
        .limit(limit)
    )
    if busy:
        stmt = stmt.where(WebhookDelivery.endpoint_id.not_in(sorted(busy)))
    return list(session.exec(stmt).all())


@dataclass(frozen=True)
class _Message:
    delivery_id: str
    endpoint_name: str
    url: str
    secret: str
    body: bytes


def _next_message(endpoint_id: str, now: datetime) -> _Message | None:
    """The endpoint's oldest pending delivery if it is due, else None.

    Later rows never overtake it: while the oldest one waits for its retry, the
    endpoint sends nothing. Rows that must not be sent any more are abandoned here.
    """
    with Session(db_engine.engine) as session:
        endpoint = session.get(WebhookEndpoint, endpoint_id)
        if endpoint is None:
            return None
        while True:
            row = session.exec(
                select(WebhookDelivery)
                .where(WebhookDelivery.endpoint_id == endpoint_id)
                .where(*_pending())
                .order_by(WebhookDelivery.created_at, _ROWID)
                .limit(1)
            ).first()
            if row is None or clock.as_utc(row.next_attempt_at) > now:
                return None
            reason = _abandon_reason(row, endpoint, now)
            if reason is None:
                return _Message(row.id, endpoint.name, endpoint.url, endpoint.secret, row.payload_json.encode("utf-8"))
            _abandon(session, row, endpoint, reason)
            session.commit()


def _record(message: _Message, status: int | None, error: str | None, now: datetime) -> bool:
    """Store one attempt's outcome; return whether the delivery is now delivered."""
    with Session(db_engine.engine) as session:
        row = session.get(WebhookDelivery, message.delivery_id)
        if row is None:  # deleted by retention meanwhile
            return False
        row.attempts += 1
        row.last_status = status
        if error is None:
            row.delivered_at = now
            row.last_error = None
        elif row.last_error not in _ABANDONED:
            row.last_error = error
            row.next_attempt_at = now + retry_delay(row.attempts)
        session.add(row)
        session.commit()
    log.info("Webhook %s delivery %s: %s", message.endpoint_name, message.delivery_id,
             "delivered" if error is None else error)
    return error is None


async def _post(client: httpx.AsyncClient, message: _Message) -> tuple[int | None, str | None]:
    """POST one message; return ``(status, error)``, ``error`` None on a 2xx answer.

    The URL is validated again first (its host may resolve elsewhere by now).
    Redirects are not followed and count as failures; the answer body is not read.
    """
    try:
        await asyncio.to_thread(validate_url, message.url)
    except InvalidWebhookUrl:
        return None, "invalid_url"
    timestamp = int(clock.now().timestamp())
    headers = {
        "content-type": "application/json",
        "user-agent": f"resona-hub/{releases.hub_release_id()}",
        "webhook-id": message.delivery_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(message.secret, message.delivery_id, timestamp, message.body),
    }
    try:
        async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
            async with client.stream("POST", message.url, content=message.body, headers=headers) as response:
                status = response.status_code
    except (TimeoutError, httpx.TimeoutException):
        return None, "timeout"
    except httpx.HTTPError:
        return None, "connection_error"
    return (status, None) if 200 <= status < 300 else (status, f"http_{status}")


class Dispatcher:
    """Sends due deliveries: per endpoint one task at a time, endpoints concurrently.

    All state is in the outbox; a new dispatcher (after a restart) resumes every
    pending row. A delivery interrupted mid-send is sent again (at-least-once).
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=SEND_TIMEOUT_SECONDS, follow_redirects=False, transport=transport
        )
        self._sending: dict[str, asyncio.Task] = {}

    def _due(self, now: datetime, busy: set[str]) -> list[str]:
        with Session(db_engine.engine) as session:
            abandon_stale(session, now)
            return due_endpoints(session, now, busy)

    async def tick(self) -> int:
        """Abandon stale rows and start a sender for every idle endpoint with due rows."""
        endpoint_ids = await asyncio.to_thread(self._due, clock.now(), set(self._sending))
        for endpoint_id in endpoint_ids:
            task = asyncio.create_task(self._drain(endpoint_id))
            self._sending[endpoint_id] = task
            task.add_done_callback(lambda _task, key=endpoint_id: self._sending.pop(key, None))
        return len(endpoint_ids)

    async def _drain(self, endpoint_id: str) -> None:
        """Send the endpoint's due rows in creation order until one fails or none is due."""
        try:
            while True:
                message = await asyncio.to_thread(_next_message, endpoint_id, clock.now())
                if message is None:
                    return
                status, error = await _post(self._client, message)
                if not await asyncio.to_thread(_record, message, status, error, clock.now()):
                    return
        except Exception as exc:  # never the URL or secret: only the exception type
            log.error("Webhook dispatch for endpoint %s failed: %s", endpoint_id, type(exc).__name__)

    async def wait_idle(self) -> None:
        """Wait until every running sender has finished."""
        while self._sending:
            await asyncio.gather(*list(self._sending.values()), return_exceptions=True)

    async def aclose(self) -> None:
        """Cancel running senders (their rows stay pending) and close the HTTP client."""
        tasks = list(self._sending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()


async def dispatch_loop(
    interval: float = DISPATCH_INTERVAL_SECONDS, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Run :meth:`Dispatcher.tick` every ``interval`` seconds until cancelled."""
    dispatcher = Dispatcher(transport)
    try:
        while True:
            try:
                await dispatcher.tick()
            except Exception as exc:
                log.error("Webhook dispatch pass failed: %s", type(exc).__name__)
            await asyncio.sleep(interval)
    finally:
        await dispatcher.aclose()
