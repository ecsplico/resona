"""Execution policy: the one seam deciding whether a processing stage may run.

Hub, worker and explicit local CLI mode all call :func:`check_policy` before a
job starts and again right before every stage that talks to an endpoint. A
denial raises :class:`PolicyDenied`; callers must surface it as
``policy_denied`` and never substitute another provider or skip the check.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator


class EndpointKind(StrEnum):
    LOCAL_LLM = "local_llm"
    CLOUD_ASR = "cloud_asr"
    CLOUD_LLM = "cloud_llm"


CLOUD_KINDS = frozenset({EndpointKind.CLOUD_ASR, EndpointKind.CLOUD_LLM})


class Endpoint(BaseModel):
    """An admin-registered processing destination outside the in-process engine.

    ``url`` is fixed by the registry entry (``None`` for provider SDK defaults of
    cloud ASR adapters); ``worker_id`` is the worker holding its secret file.
    ``unload_url`` (``local_llm`` only, loopback http(s) only) is POSTed by the
    worker after a job's last stage on this endpoint so the model server frees
    the GPU for the next job; it carries no content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: EndpointKind
    model: str
    url: str | None = None
    worker_id: str | None = None
    unload_url: str | None = None

    @model_validator(mode="after")
    def _unload_url_is_local(self) -> "Endpoint":
        if self.unload_url is None:
            return self
        if self.kind is not EndpointKind.LOCAL_LLM:
            raise ValueError("unload_url is only allowed on local_llm endpoints")
        if urlsplit(self.unload_url).scheme not in ("http", "https") or not is_loopback_url(self.unload_url):
            raise ValueError("unload_url must be a loopback http(s) URL")
        return self


StageKind = Literal["asr", "ocr", "llm"]


@dataclass(frozen=True)
class Stage:
    """One step of a resolved processing chain.

    ``endpoint=None`` means the stage runs in-process on the approved worker
    with locally prepared weights (no egress).
    """

    name: str
    kind: StageKind
    endpoint: Endpoint | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    private: bool = True
    cloud_enabled: bool = False
    approved_endpoint_ids: tuple[str, ...] = ()
    endpoints: tuple[Endpoint, ...] = ()

    def endpoint(self, endpoint_id: str) -> Endpoint | None:
        for ep in self.endpoints:
            if ep.id == endpoint_id:
                return ep
        return None


class PolicyDenied(Exception):
    """A stage is not allowed under the current execution policy."""

    code = "policy_denied"

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


def is_loopback_url(url: str | None) -> bool:
    if not url:
        return False
    host = urlsplit(url).hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_policy(policy: ExecutionPolicy, stages: Iterable[Stage]) -> None:
    """Raise :class:`PolicyDenied` for the first stage the policy forbids."""
    for stage in stages:
        ep = stage.endpoint
        if ep is None:
            continue
        if ep.id not in policy.approved_endpoint_ids:
            raise PolicyDenied(stage.name, f"endpoint {ep.id!r} is not approved")
        registered = policy.endpoint(ep.id)
        if registered is None or registered != ep:
            raise PolicyDenied(stage.name, f"endpoint {ep.id!r} does not match its registry entry")
        if ep.kind in CLOUD_KINDS:
            if policy.private:
                raise PolicyDenied(stage.name, "private job cannot use a cloud endpoint")
            if not policy.cloud_enabled:
                raise PolicyDenied(stage.name, "cloud processing is disabled for this organization")
        elif ep.kind is EndpointKind.LOCAL_LLM and not is_loopback_url(ep.url):
            raise PolicyDenied(stage.name, "local_llm endpoint must be a loopback URL")
