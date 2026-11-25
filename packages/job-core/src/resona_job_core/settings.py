"""Revisioned organization settings: execution profiles, defaults, endpoints.

The hub stores one :class:`OrganizationSettings` document per revision and
organization; jobs snapshot the resolved candidate, never the mutable default.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .policy import CLOUD_ASR_ENGINES, Endpoint, ExecutionPolicy

# ``external``: the engine runs at a cloud provider the worker calls (cloud ASR).
Accelerator = Literal["metal", "cuda", "cpu", "external"]
# The release gate measures quality per platform, named after the ASR/layout accelerator.
Platform = Literal["mac-metal", "linux-cuda", "cpu", "cloud"]
PLATFORMS: tuple[str, ...] = ("mac-metal", "linux-cuda", "cpu", "cloud")
PLATFORM_OF_ACCELERATOR: dict[str, str] = {"metal": "mac-metal", "cuda": "linux-cuda", "cpu": "cpu", "external": "cloud"}
# ``text`` jobs run only a postprocess profile on uploaded text: no engine, no candidates.
JobKind = Literal["audio", "document", "text"]

TEXT_EXECUTION_PROFILE = "-"
"""``execution_profile`` recorded for text jobs, which run no execution profile.
Reserved: :class:`OrganizationSettings` rejects it as an execution profile name."""

MODEL_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
"""Name of a model role (``OrganizationSettings.model_roles`` key, ``@<role>`` in a profile step)."""
STANDARD_MODEL_ROLES: tuple[str, ...] = ("polish", "cleanup", "title", "extract", "judge")
"""Roles the catalogue profiles use. Documented, not enforced: an organization may define others."""

OMITTED_WHEN_UNSET: tuple[str, ...] = ("model_roles", "live")
"""``OrganizationSettings`` fields left out of a dump while empty, for older releases."""


class Candidate(BaseModel):
    """One approved (engine, model, revision, accelerator, ocr_engine) tuple."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine: str
    model: str
    model_revision: str | None = None
    accelerator: Accelerator
    ocr_engine: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_cloud_accelerator(cls, data: object) -> object:
        # Settings and job snapshots stored before accelerator 'external' hold cloud
        # candidates on 'cpu'; read them as 'external' instead of failing.
        if isinstance(data, dict) and data.get("engine") in CLOUD_ASR_ENGINES and data.get("accelerator") != "external":
            return {**data, "accelerator": "external"}
        return data

    @model_validator(mode="after")
    def _cloud_runs_external(self) -> Candidate:
        if (self.engine in CLOUD_ASR_ENGINES) != (self.accelerator == "external"):
            raise ValueError("cloud ASR engines, and only they, run on accelerator 'external'")
        return self


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: list[Candidate] = Field(default_factory=list)
    document: list[Candidate] = Field(default_factory=list)
    # Default for production audio jobs on this profile that do not set ``diarize``
    # themselves; a job's explicit ``diarize`` (true or false) always wins.
    diarize: bool = False

    @model_serializer(mode="wrap")
    def _omit_default_diarize(self, handler: SerializerFunctionWrapHandler) -> dict:
        # Left out while false, so settings documents stay readable by releases predating it
        # (worker ``prepare --settings-file``, CLI settings cache: ``extra="forbid"``).
        dumped = handler(self)
        if dumped.get("diarize") is False:
            del dumped["diarize"]
        return dumped

    def candidates(self, kind: JobKind) -> list[Candidate]:
        if kind == "text":
            return []
        return self.audio if kind == "audio" else self.document


# Same syntax as a hosted profile name (resona-api ``PROFILE_NAME_RE``).
_LIVE_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class LiveDefaults(BaseModel):
    """Organization defaults for live dictation; a device setting left empty inherits them.

    ``engine``: the ``WS /v1/listen`` engine when a request names none (before
    ``RESONA_DEFAULT_ENGINE``). ``correction_execution_profile``: the execution profile of
    chunked live correction jobs. ``cleanup_profile``: the stored postprocess profile a
    client runs on finished live text as a text job.
    """

    model_config = ConfigDict(extra="forbid")

    engine: str | None = Field(default=None, min_length=1)
    correction_execution_profile: str | None = Field(default=None, min_length=1)
    cleanup_profile: str | None = None

    @model_validator(mode="after")
    def _cleanup_profile_name(self) -> LiveDefaults:
        if self.cleanup_profile is not None and not _LIVE_PROFILE_NAME_RE.fullmatch(self.cleanup_profile):
            raise ValueError(f"live.cleanup_profile {self.cleanup_profile!r} is not a profile name")
        return self

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler):
        # No return annotation (OpenAPI keeps the field list). Unset keys are left out,
        # so an empty ``live`` dumps as ``{}`` and the organization dump drops it.
        return {key: value for key, value in handler(self).items() if value is not None}


class OrganizationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = "de"
    polish: bool = False
    postprocess_profile: str | None = None
    cloud_enabled: bool = False
    cpu_fallback_enabled: bool = False
    testbench_enabled: bool = False
    default_execution_profile: str = "private-auto"
    execution_profiles: dict[str, ExecutionProfile] = Field(default_factory=dict)
    endpoints: list[Endpoint] = Field(default_factory=list)
    # Model roles: role name -> endpoint id. A postprocess step says ``"endpoint": "@<role>"``
    # and admission replaces it with the id, so the pinned profile names a concrete endpoint.
    model_roles: dict[str, str] = Field(default_factory=dict)
    # Live dictation defaults (engine, correction execution profile, cleanup profile).
    live: LiveDefaults | None = None

    @model_validator(mode="after")
    def _reserved_profile_name(self) -> OrganizationSettings:
        if TEXT_EXECUTION_PROFILE in self.execution_profiles or self.default_execution_profile == TEXT_EXECUTION_PROFILE:
            raise ValueError(f"execution profile name {TEXT_EXECUTION_PROFILE!r} is reserved for text jobs")
        return self

    @model_validator(mode="after")
    def _model_roles_target_endpoints(self) -> OrganizationSettings:
        known = {ep.id for ep in self.endpoints}
        for role, endpoint_id in self.model_roles.items():
            if not MODEL_ROLE_RE.fullmatch(role):
                raise ValueError(f"model role name {role!r} must match {MODEL_ROLE_RE.pattern}")
            if endpoint_id not in known:
                raise ValueError(f"model role {role!r} names endpoint {endpoint_id!r}, which is not in endpoints")
        return self

    @model_validator(mode="after")
    def _live_correction_profile_known(self) -> OrganizationSettings:
        name = self.live.correction_execution_profile if self.live else None
        if name is not None and name not in self.execution_profiles:
            raise ValueError(f"live.correction_execution_profile {name!r} is not in execution_profiles")
        return self

    @model_serializer(mode="wrap")
    def _omit_unset_fields(self, handler: SerializerFunctionWrapHandler):
        # No return annotation: the OpenAPI output schema stays the field list.
        # Fields added after 0.1 are left out while unset, so settings documents stay
        # readable by releases predating them (``extra="forbid"`` there).
        dumped = handler(self)
        for name in OMITTED_WHEN_UNSET:
            if name in dumped and not dumped[name]:
                del dumped[name]
        return dumped

    def policy(self, *, private: bool) -> ExecutionPolicy:
        return ExecutionPolicy(
            private=private,
            cloud_enabled=self.cloud_enabled,
            approved_endpoint_ids=tuple(ep.id for ep in self.endpoints),
            endpoints=tuple(self.endpoints),
        )


_MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
_MAC_DOC = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
_CUDA_AUDIO = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
_CUDA_DOC = Candidate(engine="docling", model="docling-standard", accelerator="cuda", ocr_engine="rapidocr")
_CPU_AUDIO = Candidate(engine="faster-whisper", model="Systran/faster-whisper-large-v3", accelerator="cpu")
_CPU_DOC = Candidate(engine="docling", model="docling-standard", accelerator="cpu", ocr_engine="rapidocr")


def default_settings() -> OrganizationSettings:
    """Seed settings for a new organization (revision 1)."""
    return OrganizationSettings(
        execution_profiles={
            "private-auto": ExecutionProfile(audio=[_MAC_AUDIO, _CUDA_AUDIO], document=[_MAC_DOC, _CUDA_DOC]),
            "mac-private": ExecutionProfile(audio=[_MAC_AUDIO], document=[_MAC_DOC]),
            "linux-cuda": ExecutionProfile(audio=[_CUDA_AUDIO], document=[_CUDA_DOC]),
            "cpu-private": ExecutionProfile(audio=[_CPU_AUDIO], document=[_CPU_DOC]),
        }
    )
