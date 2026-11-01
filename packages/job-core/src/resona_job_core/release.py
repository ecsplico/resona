"""Release manifest: the immutable pin set of one Resona release.

A release pins every wheel by version and SHA-256, every container image by
``sha256:`` digest and every model by an exact revision. Mutable references
(``:latest``, ``:cuda``, ``main``) are rejected, so a manifest always names
exactly one set of bytes. The hub stages manifests (``resona-admin release
stage``); workers install from them (``resona-worker update``).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORKER_PROTOCOL = 1

RELEASE_ID_PATTERN = r"^[0-9][0-9A-Za-z.+-]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
# registry[:port]/path/name without a tag or digest suffix.
_IMAGE_NAME = r"^[a-z0-9][a-z0-9.-]*(:[0-9]+)?(/[a-z0-9][a-z0-9._-]*)+$"
_WHEEL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_HF_REPO = re.compile(r"^[^/\s]+/[^/\s]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MUTABLE_REVISIONS = frozenset({"main", "master", "head", "latest", "stable", "cuda", "cpu"})


class WheelPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_WHEEL_NAME)
    version: str = Field(pattern=r"^[0-9][0-9A-Za-z.+!-]{0,63}$")
    sha256: str = Field(pattern=_SHA256)


class ImagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=_IMAGE_NAME, description="Repository without tag, e.g. ghcr.io/ecsplico/resona-hub")
    digest: str = Field(pattern=_DIGEST)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.digest}"


class ModelPin(BaseModel):
    """One model at an exact revision. HuggingFace repos (``owner/name``) need a commit hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    revision: str = Field(pattern=r"^[0-9A-Za-z._+-]{1,128}$")

    @model_validator(mode="after")
    def _immutable(self) -> ModelPin:
        if self.revision.lower() in _MUTABLE_REVISIONS:
            raise ValueError(f"model revision {self.revision!r} is a mutable reference")
        if _HF_REPO.match(self.model) and not _COMMIT.match(self.revision):
            raise ValueError(f"HuggingFace model {self.model} must be pinned to a 40-hex commit hash")
        return self


class ConstraintsPin(BaseModel):
    """A pip constraints file exported from ``uv.lock`` (third-party versions the worker
    was tested with), shipped next to the wheels. Without it a fresh environment would
    resolve whatever its index serves that day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.txt$")
    sha256: str = Field(pattern=_SHA256)


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: str = Field(pattern=RELEASE_ID_PATTERN)
    created_at: datetime
    wheels: list[WheelPin] = Field(min_length=1)
    images: list[ImagePin] = Field(default_factory=list)
    models: list[ModelPin] = Field(default_factory=list)
    constraints: ConstraintsPin | None = None
    worker_protocol: Literal[1] = WORKER_PROTOCOL

    @field_validator("wheels", "images", "models")
    @classmethod
    def _unique(cls, items: list) -> list:
        keys = [(getattr(i, "name", None), getattr(i, "model", None), getattr(i, "engine", None)) for i in items]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate entries")
        return items

    def model_pins(self) -> dict[str, str]:
        """``model -> revision`` for every pinned model."""
        return {pin.model: pin.revision for pin in self.models}

    def wheel(self, name: str) -> WheelPin | None:
        wanted = _normalize(name)
        return next((w for w in self.wheels if _normalize(w.name) == wanted), None)

    def canonical_json(self) -> str:
        """The canonical serialization the hub stores and both sides hash."""
        return self.model_dump_json()

    def digest(self) -> str:
        """SHA-256 of :meth:`canonical_json`; hub and worker compare manifests by this."""
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


__all__ = [
    "ConstraintsPin",
    "ImagePin",
    "ModelPin",
    "RELEASE_ID_PATTERN",
    "ReleaseManifest",
    "WORKER_PROTOCOL",
    "WheelPin",
]
