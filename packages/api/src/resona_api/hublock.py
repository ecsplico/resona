"""Single-hub process lock beside the database.

The hub holds an exclusive ``flock`` on ``<DB_PATH>/hub.lock`` for its whole
lifetime. Offline operations that replace the database (``resona-admin
restore``) take the same lock without waiting, so they refuse while a hub
process that uses this data volume is running. The lock dies with the process,
so a crashed hub never leaves a stale lock behind.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .paths import DB_PATH

LOCK_NAME = "hub.lock"


class HubRunning(RuntimeError):
    """Another process (normally the running hub) holds the hub lock."""


def lock_path() -> Path:
    return DB_PATH / LOCK_NAME


class HubLock:
    """An acquired hub lock; :meth:`release` (or process exit) frees it."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "HubLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire(path: Path | None = None) -> HubLock:
    """Take the hub lock without waiting; raise :class:`HubRunning` when it is held."""
    target = path or lock_path()
    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise HubRunning("the hub is running (hub lock held); stop it first") from None
    return HubLock(fd)


__all__ = ["HubLock", "HubRunning", "LOCK_NAME", "acquire", "lock_path"]
