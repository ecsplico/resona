"""The only time source for lease, expiry and retention logic.

Tests monkeypatch :func:`now`. Code that decides expiry calls ``clock.now()``
through the module, never ``datetime.now`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as aware UTC. SQLite returns naive datetimes that were stored as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
