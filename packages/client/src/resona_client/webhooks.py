"""Verify job webhooks the hub sends (Standard Webhooks).

A receiving backend calls :func:`verify` with the request headers, the exact body
bytes and the endpoint's signing secret (``whsec_…``, shown once by
``create_webhook``/``rotate_webhook``). Pure: no network, no clock other than
``time.time()`` (or the ``now`` argument).

The signature is computed with :func:`resona_job_core.webhooks.sign`, the same
function the hub's dispatcher uses, so both sides cannot drift.
"""

from __future__ import annotations

import hmac
import time
from typing import Mapping, Optional

from pydantic import ValidationError
from resona_job_core.webhooks import SIGNATURE_VERSION, JobEvent, sign

ID_HEADER = "webhook-id"
TIMESTAMP_HEADER = "webhook-timestamp"
SIGNATURE_HEADER = "webhook-signature"
DEFAULT_TOLERANCE_SECONDS = 300


class InvalidWebhookSignature(Exception):
    """The request is not a valid, fresh job webhook signed with this secret."""


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    raise InvalidWebhookSignature(f"missing {name} header")


def verify(
    headers: Mapping[str, str],
    body: bytes,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> JobEvent:
    """Check a webhook's signature and freshness, then return its :class:`JobEvent`.

    ``headers`` is any mapping (header names compared case-insensitively); ``body``
    must be the raw request bytes, not re-serialized JSON. The signature header may
    list several space-separated ``v1,<base64>`` signatures; one match suffices
    (constant-time compare). A ``webhook-timestamp`` more than ``tolerance_seconds``
    away from ``now`` (default: the current time) is rejected in both directions.
    Raises :class:`InvalidWebhookSignature` on any failure, including a body that is
    not a valid ``JobEvent``.
    """
    msg_id = _header(headers, ID_HEADER)
    raw_timestamp = _header(headers, TIMESTAMP_HEADER)
    signatures = _header(headers, SIGNATURE_HEADER)
    try:
        timestamp = int(raw_timestamp)
    except ValueError:
        raise InvalidWebhookSignature("webhook-timestamp is not an integer") from None
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise InvalidWebhookSignature("webhook-timestamp is outside the tolerance")
    try:
        expected = sign(secret, msg_id, timestamp, body)
    except ValueError as exc:
        raise InvalidWebhookSignature(str(exc)) from None
    matched = False
    for candidate in signatures.split():
        if candidate.split(",", 1)[0] != SIGNATURE_VERSION:
            continue
        if hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
            matched = True
    if not matched:
        raise InvalidWebhookSignature("no matching signature")
    try:
        return JobEvent.model_validate_json(body)
    except ValidationError:
        raise InvalidWebhookSignature("body is not a job event") from None


__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "ID_HEADER",
    "InvalidWebhookSignature",
    "JobEvent",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "verify",
]
