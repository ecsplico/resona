"""Local drain handshake between ``resona-worker update`` and a running supervisor.

Both sides share only the worker's work directory; nothing here is reachable from
the hub, so an update can only be started by someone on the worker machine.

``update`` writes ``drain``. The supervisor checks it before every claim cycle, so
a job in progress always finishes first; once idle it writes ``drained`` and
stops claiming. ``update`` waits for ``drained``, does its work, then removes
``drain``; the supervisor removes ``drained`` and claims again. When an update
succeeded without a service manager to restart the worker, ``update`` first writes
``superseded``: the supervisor then exits (code 0) instead of claiming on old code.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

DRAIN_REQUEST = "drain"
DRAINED = "drained"
SUPERSEDED = "superseded"
SUPERVISOR = "supervisor.pid"


class DrainTimeout(RuntimeError):
    """The worker did not report idle in time."""


def drain_requested(work_dir: Path) -> bool:
    return (work_dir / DRAIN_REQUEST).exists()


def request_drain(work_dir: Path) -> None:
    """Ask the supervisor to stop claiming. Clears a stale ``drained`` marker first."""
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (work_dir / DRAINED).unlink(missing_ok=True)
    (work_dir / DRAIN_REQUEST).write_text(f"{os.getpid()}\n", encoding="utf-8")


def release_drain(work_dir: Path) -> None:
    (work_dir / DRAIN_REQUEST).unlink(missing_ok=True)


def supersede(work_dir: Path) -> None:
    """Tell the drained supervisor its release was replaced: exit instead of claiming again.
    No-op when no supervisor is running, so a stale marker cannot stop the next one."""
    if not supervisor_alive(work_dir):
        return
    tmp = work_dir / f".{SUPERSEDED}.tmp"
    tmp.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.replace(tmp, work_dir / SUPERSEDED)


def take_superseded(work_dir: Path) -> bool:
    """Supervisor side: consume a ``superseded`` marker; True if one was present."""
    try:
        (work_dir / SUPERSEDED).unlink()
    except FileNotFoundError:
        return False
    return True


def mark_drained(work_dir: Path) -> None:
    """Supervisor side: report idle (no job running, no further claims)."""
    marker = work_dir / DRAINED
    if not marker.exists():
        tmp = work_dir / f".{DRAINED}.tmp"
        tmp.write_text(f"{os.getpid()}\n", encoding="utf-8")
        os.replace(tmp, marker)


def clear_drained(work_dir: Path) -> None:
    (work_dir / DRAINED).unlink(missing_ok=True)


def mark_running(work_dir: Path) -> None:
    """Supervisor side: record this process as the work directory's supervisor."""
    tmp = work_dir / f".{SUPERVISOR}.tmp"
    tmp.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.replace(tmp, work_dir / SUPERVISOR)


def clear_running(work_dir: Path) -> None:
    marker = work_dir / SUPERVISOR
    try:
        if int(marker.read_text(encoding="utf-8")) == os.getpid():
            marker.unlink()
    except (OSError, ValueError):
        pass


def supervisor_alive(work_dir: Path) -> bool:
    """Whether the recorded supervisor process still exists."""
    try:
        pid = int((work_dir / SUPERVISOR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_drained(
    work_dir: Path,
    *,
    timeout: float,
    poll: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    alive: Callable[[Path], bool] = supervisor_alive,
) -> None:
    """Return once the supervisor reports idle, or once no supervisor is running at all
    (it crashed or was stopped: nothing can be claiming, and the drain request stays in
    place, so a supervisor started meanwhile drains before its first claim)."""
    deadline = monotonic() + timeout
    while not (work_dir / DRAINED).exists():
        if not alive(work_dir):
            return
        if monotonic() >= deadline:
            raise DrainTimeout(f"worker in {work_dir} did not drain within {timeout:.0f}s")
        sleep(poll)


__all__ = [
    "DRAINED",
    "DRAIN_REQUEST",
    "DrainTimeout",
    "SUPERSEDED",
    "SUPERVISOR",
    "clear_drained",
    "clear_running",
    "drain_requested",
    "mark_drained",
    "mark_running",
    "release_drain",
    "request_drain",
    "supersede",
    "supervisor_alive",
    "take_superseded",
    "wait_drained",
]
