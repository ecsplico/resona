"""Revisioned organization settings: execution profiles, defaults, endpoints.

The hub stores one :class:`OrganizationSettings` document per revision and
organization; jobs snapshot the resolved candidate, never the mutable default.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .policy import Endpoint, ExecutionPolicy

Accelerator = Literal["metal", "cuda", "cpu"]
# ``text`` jobs run only a postprocess profile on uploaded text: no engine, no candidates.
JobKind = Literal["audio", "document", "text"]


class Candidate(BaseModel):
    """One approved (engine, model, revision, accelerator, ocr_engine) tuple."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine: str
    model: str
    model_revision: str | None = None
    accelerator: Accelerator
    ocr_engine: str | None = None


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: list[Candidate] = Field(default_factory=list)
    document: list[Candidate] = Field(default_factory=list)

    def candidates(self, kind: JobKind) -> list[Candidate]:
        if kind == "text":
            return []
        return self.audio if kind == "audio" else self.document


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
