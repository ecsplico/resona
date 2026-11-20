"""Job webhook contract shared by the hub and its clients.

:class:`JobEvent` is the frozen payload the hub sends when a job ends: job id,
kind, status, error code and time, nothing a user wrote or uploaded. :func:`sign`
computes the Standard Webhooks signature the hub's dispatcher sends and a client
verifies.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SECRET_PREFIX = "whsec_"
SIGNATURE_VERSION = "v1"

JobEventType = Literal["job.completed", "job.failed", "job.cancelled"]
TerminalStatus = Literal["completed", "failed", "cancelled"]

EVENT_OF_STATUS: dict[str, str] = {
    "completed": "job.completed",
    "failed": "job.failed",
    "cancelled": "job.cancelled",
}
"""Event type of each terminal job status."""

ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
"""Error codes are machine identifiers (``deadline_exceeded``), never free text."""


class JobEvent(BaseModel):
    """Payload of a job webhook. Every field is an identifier, enum or time, so no
    field can carry a transcript, file name, instructions or an error message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: JobEventType
    job_id: int = Field(ge=1)
    kind: Literal["audio", "document", "text"]
    status: TerminalStatus
    error_code: Optional[str] = Field(default=None, pattern=ERROR_CODE_PATTERN)
    finished_at: datetime

    @model_validator(mode="after")
    def _type_matches_status(self) -> "JobEvent":
        if EVENT_OF_STATUS[self.status] != self.type:
            raise ValueError(f"event type {self.type} does not match status {self.status}")
        if self.status == "completed" and self.error_code is not None:
            raise ValueError("a completed job has no error code")
        return self


def secret_key(secret: str) -> bytes:
    """HMAC key of a signing secret.

    A ``whsec_``-prefixed secret (the format the hub issues) is the base64 of the
    key bytes: the prefix is stripped and the rest decoded. Any other string is used
    as its UTF-8 bytes. Raises ``ValueError`` for a ``whsec_`` secret that is not
    valid base64.
    """
    if secret.startswith(SECRET_PREFIX):
        try:
            return base64.b64decode(secret[len(SECRET_PREFIX):], validate=True)
        except binascii.Error as exc:
            raise ValueError("webhook secret is not valid base64") from exc
    return secret.encode("utf-8")


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """Standard Webhooks signature of one message: ``v1,<base64 HMAC-SHA256>``.

    The signed content is ``f"{msg_id}.{timestamp}."`` followed by the exact body
    bytes; ``timestamp`` is Unix seconds (the ``webhook-timestamp`` header). The key
    comes from :func:`secret_key`.
    """
    content = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret_key(secret), content, hashlib.sha256).digest()
    return f"{SIGNATURE_VERSION},{base64.b64encode(digest).decode('ascii')}"


__all__ = [
    "ERROR_CODE_PATTERN",
    "EVENT_OF_STATUS",
    "JobEvent",
    "JobEventType",
    "SECRET_PREFIX",
    "SIGNATURE_VERSION",
    "TerminalStatus",
    "secret_key",
    "sign",
]
