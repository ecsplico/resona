"""Composable postprocessing pipeline producing text + structured data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import partial
from importlib import resources
from pathlib import Path
from typing import Callable

from resona_job_core.policy import Endpoint, ExecutionPolicy, PolicyDenied, Stage, check_policy

from .llm import (
    LlmCallSettings,
    PostprocessStepFailed,
    legacy_endpoint_id,
    legacy_step_endpoint,
    llm_extract,
    llm_transform,
    llm_transform_chunked,
)
from .profile import Profile
from .replacements import apply_replacements

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepOutput:
    """Text step output that also carries content-free warnings."""

    text: str
    warnings: tuple[str, ...] = ()


TextStep = Callable[[str], "str | StepOutput"]
ExtractStep = Callable[[str], dict]

_LLM_STEP_TYPES = ("llm", "extract")


@dataclass
class PostprocessResult:
    """Outcome of a pipeline run."""

    text: str
    data: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class PostprocessPipeline:
    """Ordered chain of text transforms and structured-extraction steps."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, str, Callable]] = []

    def add_text(self, name: str, step: TextStep) -> "PostprocessPipeline":
        self._steps.append((name, "text", step))
        return self

    def add_extract(self, name: str, step: ExtractStep) -> "PostprocessPipeline":
        self._steps.append((name, "extract", step))
        return self

    def run(self, text: str) -> PostprocessResult:
        """Run every step.

        ``PolicyDenied`` and ``PostprocessStepFailed`` propagate; other failing steps are
        logged and skipped.
        """
        result = PostprocessResult(text=text)
        for name, kind, step in self._steps:
            try:
                if kind == "text":
                    out = step(result.text)
                    if isinstance(out, StepOutput):
                        result.text = out.text
                        result.warnings.extend(out.warnings)
                    else:
                        result.text = out
                else:
                    result.data[name] = step(result.text)
            except (PolicyDenied, PostprocessStepFailed):
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Postprocess step %r failed, skipping: %s", name, e)
                result.data.setdefault("_skipped", []).append(name)
        return result


def _load_rules(step: dict, base_dir: Path | None) -> list[dict]:
    """Resolve a replacements step's rules from inline `rules` or `source`."""
    if "rules" in step:
        return step["rules"]
    source = step.get("source", "")
    if base_dir is not None:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    # Fall back to a file bundled in resona-postprocess (e.g. default_replacements.json).
    ref = resources.files("resona_postprocess").joinpath(source)
    if ref.is_file():
        return json.loads(ref.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"replacements source not found: {source}")


def _resolve_endpoint(step: dict, policy: ExecutionPolicy) -> Endpoint:
    """Resolve an llm/extract step to an endpoint the policy knows about.

    ``endpoint: <id>`` names a registry entry. A legacy step (only ``model``/
    ``api_base``) must match the endpoint derived from it; anything else is denied.
    """
    name = step.get("name", step["type"])
    endpoint_id = step.get("endpoint")
    if endpoint_id is not None:
        ep = policy.endpoint(endpoint_id)
        if ep is None:
            raise PolicyDenied(name, f"endpoint {endpoint_id!r} is not in the policy registry")
        return ep
    derived = legacy_step_endpoint(step)
    registered = policy.endpoint(legacy_endpoint_id(step))
    if registered is None or registered != derived:
        raise PolicyDenied(name, "legacy model endpoint is not in the policy registry")
    return registered


def step_endpoint_id(step: dict) -> str:
    """ID of the endpoint an llm/extract step calls: its ``endpoint``, else the legacy-derived ID.

    Raises :class:`PolicyDenied` for a legacy step without a resolvable model.
    """
    return step.get("endpoint") or legacy_endpoint_id(step)


def endpoint_ids(profile: Profile) -> list[str]:
    """IDs of the endpoints this profile's llm/extract steps call, in step order, without duplicates."""
    ids = (step_endpoint_id(step) for step in profile.steps if step.get("type") in _LLM_STEP_TYPES)
    return list(dict.fromkeys(ids))


def local_policy(profile: Profile, *, base: ExecutionPolicy) -> ExecutionPolicy:
    """Policy for explicit local CLI mode: ``base`` plus this profile's legacy-derived endpoints.

    ``base`` comes from the organization's (cached) settings, so privacy and
    ``cloud_enabled`` stay the organization's; a profile file can only add its own
    endpoints, which ``check_policy`` still gates. The hub never uses this.
    """
    endpoints: dict[str, Endpoint] = {ep.id: ep for ep in base.endpoints}
    for step in profile.steps:
        if step.get("type") in _LLM_STEP_TYPES and "endpoint" not in step:
            ep = legacy_step_endpoint(step)
            endpoints.setdefault(ep.id, ep)
    return ExecutionPolicy(
        private=base.private,
        cloud_enabled=base.cloud_enabled,
        approved_endpoint_ids=tuple(dict.fromkeys((*base.approved_endpoint_ids, *endpoints))),
        endpoints=tuple(endpoints.values()),
    )


def build_pipeline(profile: Profile, *, policy: ExecutionPolicy) -> PostprocessPipeline:
    """Build a runnable pipeline. Every llm/extract stage is policy-checked here.

    Raises :class:`PolicyDenied` before any processing if a stage is not allowed.
    """
    endpoints: dict[int, Endpoint] = {}
    for idx, step in enumerate(profile.steps):
        if step.get("type") in _LLM_STEP_TYPES:
            endpoints[idx] = _resolve_endpoint(step, policy)
    for idx, endpoint in endpoints.items():
        step = profile.steps[idx]
        check_policy(policy, [Stage(name=step.get("name", step["type"]), kind="llm", endpoint=endpoint)])

    pipe = PostprocessPipeline()
    for idx, step in enumerate(profile.steps):
        stype = step["type"]
        name = step.get("name", stype)
        if stype == "replacements":
            rules = _load_rules(step, profile.base_dir)
            pipe.add_text(name, lambda t, r=rules: apply_replacements(t, r))
            continue
        call = {
            "prompt": step["prompt"],
            "endpoint": endpoints[idx],
            "policy": policy,
            "settings": LlmCallSettings.from_step(step),
        }
        if stype == "extract":
            pipe.add_extract(name, partial(llm_extract, **call))
        elif "chunk_chars" in step:
            chunked = partial(
                llm_transform_chunked,
                **call,
                chunk_chars=step["chunk_chars"],
                step_name=name,
                max_length_deviation=step.get("max_length_deviation"),
                preserve_markup=step.get("preserve_markup", False),
            )
            pipe.add_text(name, lambda t, f=chunked: StepOutput(*f(t)))
        else:
            pipe.add_text(name, partial(llm_transform, **call))
    return pipe
