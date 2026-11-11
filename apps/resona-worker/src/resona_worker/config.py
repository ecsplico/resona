"""Worker configuration: hub URL, credential file, prepared capabilities, work directory."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from resona_job_core import Candidate, is_loopback_url


class ConfigError(ValueError):
    """Invalid worker configuration. Messages never contain credential material."""


class WeightRecord(BaseModel):
    """One HuggingFace snapshot verified offline: repo id and resolved commit hash."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    revision: str


class PreparedCapability(BaseModel):
    """A candidate tuple that passed synthetic execution on this worker."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["audio", "document"]
    candidate: Candidate
    weights: list[WeightRecord] = Field(default_factory=list)
    seconds: float
    peak_rss_bytes: int
    peak_accelerator_bytes: int | None = None
    stage_accelerators: dict[str, str] = Field(default_factory=dict, description="actual accelerator per executed stage")


class PreparedDiarizer(BaseModel):
    """The speaker diarizer passed a synthetic two-speaker job on this worker.

    Its presence in the manifest is what lets the worker report and claim ``diarize`` jobs."""

    model_config = ConfigDict(extra="forbid")

    weights: WeightRecord
    # The audio candidate the check ran with; the diarizer runs on its accelerator.
    candidate: Candidate
    seconds: float
    accelerator: str



class CapabilitiesManifest(BaseModel):
    """Written by ``resona-worker prepare``; the only source of tuples a worker reports and claims."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profile: str
    prepared_at: datetime
    runtime_version: str
    platform: str
    tested: list[PreparedCapability]
    diarizer: PreparedDiarizer | None = None

    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(dict.fromkeys(item.candidate for item in self.tested))


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


def load_manifest(path: Path) -> CapabilitiesManifest:
    """Read a capabilities manifest written by ``resona-worker prepare``."""
    try:
        manifest = CapabilitiesManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise ConfigError(
            f"invalid capabilities file ({exc.__class__.__name__}); create it with `resona-worker prepare`"
        ) from None
    if not manifest.tested:
        raise ConfigError("capabilities file lists no tested capability; re-run `resona-worker prepare`")
    return manifest


def load_capabilities(path: Path) -> tuple[Candidate, ...]:
    """The tuples this worker prepared and tested; it reports and claims nothing else."""
    return load_manifest(path).candidates()


@dataclass(frozen=True)
class WorkerConfig:
    hub_url: str
    token: str = field(repr=False)
    work_dir: Path
    capabilities: tuple[Candidate, ...]
    # The manifest records a passed diarizer check: report and claim ``diarize`` jobs.
    diarize: bool = False
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
    "CapabilitiesManifest",
    "ConfigError",
    "PreparedCapability",
    "WeightRecord",
    "WorkerConfig",
    "load_capabilities",
    "load_manifest",
    "load_token",
    "runtime_version",
    "validate_hub_url",
]
