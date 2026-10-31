"""Worker configuration: hub URL, credential file, capabilities, work directory."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import TypeAdapter
from resona_job_core import Candidate, is_loopback_url

_CANDIDATES = TypeAdapter(list[Candidate])


class ConfigError(ValueError):
    """Invalid worker configuration. Messages never contain credential material."""


def runtime_version() -> str:
    try:
        return metadata.version("resona-worker")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def validate_hub_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return url.rstrip("/")
    if parts.scheme == "http" and parts.netloc and is_loopback_url(url):
        return url.rstrip("/")
    raise ConfigError("hub URL must be https (http is allowed only for loopback)")


def load_token(path: Path) -> str:
    """Read the worker credential from a regular 0600 file (symlinks refused)."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError("token file must be a regular file, not a symlink or directory")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise ConfigError("token file must have mode 0600")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ConfigError("token file is empty")
    return token


def load_capabilities(path: Path) -> tuple[Candidate, ...]:
    """Read the approved-to-test capability tuples this worker prepared (JSON list)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates = _CANDIDATES.validate_python(data)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"invalid capabilities file: {exc.__class__.__name__}") from None
    return tuple(dict.fromkeys(candidates))


@dataclass(frozen=True)
class WorkerConfig:
    hub_url: str
    token: str = field(repr=False)
    work_dir: Path
    capabilities: tuple[Candidate, ...]
    executor: tuple[str, ...] = (sys.executable, "-m", "resona_worker.execute")
    lease_heartbeat_seconds: float = 30.0
    claim_wait_seconds: int = 25
    poll_interval_seconds: float = 0.2

    def __post_init__(self) -> None:
        validate_hub_url(self.hub_url)
        if not self.token:
            raise ConfigError("worker token is required")
        if not 0 <= self.claim_wait_seconds <= 25:
            raise ConfigError("claim wait must be between 0 and 25 seconds")
        if self.lease_heartbeat_seconds <= 0:
            raise ConfigError("lease heartbeat interval must be positive")


__all__ = [
    "ConfigError",
    "WorkerConfig",
    "load_capabilities",
    "load_token",
    "runtime_version",
    "validate_hub_url",
]
