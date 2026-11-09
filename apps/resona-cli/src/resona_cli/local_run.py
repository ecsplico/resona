"""Explicit local mode (``--local``): the hub's policy path, executed on this machine.

Local runs use the organization settings cached by ``resona settings pull`` for the
configured hub and credential (built-in private defaults when nothing is cached),
resolve the execution profile's approved candidates exactly like hub admission,
run :func:`check_policy` on every stage, and execute the candidate through the
worker's execution subprocess (``resona_worker.execute``) with the same private
environment: offline model caches, no credentials. Postprocess LLM stages are
checked again by ``build_pipeline``. Local mode never contacts the hub, so a used
cache is marked ``stale``; it cannot enforce settings changed on the hub since.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import ValidationError
from resona_job_core import (
    Candidate,
    Endpoint,
    EndpointKind,
    JobResult,
    OrganizationSettings,
    PolicyDenied,
    Provenance,
    Stage,
    check_policy,
    default_settings,
)

from . import settings_cache
from .hub import fail, hub_identity

_PROFILES_DIR = Path.home() / ".resona" / "profiles"
# Engines that call a provider API; local mode treats them as cloud egress, like the hub.
_CLOUD_ASR_ENGINES = frozenset({"deepgram", "openai", "elevenlabs"})
_EXIT_MESSAGES = {
    2: "unsupported_input: the file could not be read or converted",
    3: "engine_error: the engine failed",
    4: "unsupported_capability: engine or prepared model weights not available on this machine "
       "(prepare them with `resona-worker prepare`)",
    5: "out_of_memory",
    6: "invalid_snapshot",
}


class LocalRunError(Exception):
    pass


@dataclass(frozen=True)
class LocalSettings:
    settings: OrganizationSettings
    revision: Optional[int]
    organization_id: Optional[str]


@dataclass(frozen=True)
class LocalPlan:
    kind: str
    execution_profile: str
    candidate: Optional[Candidate]  # None for text (postprocess-only) runs
    language: str
    options: dict[str, Any]
    private: bool
    pipeline: Any  # PostprocessPipeline | None
    postprocess_profile: Optional[str]
    settings_revision: Optional[int]


def load_local_settings(cache_dir: Optional[Path] = None) -> LocalSettings:
    """Cached settings for the configured hub+credential (marked stale), else built-in defaults."""
    identity = hub_identity()
    entry = settings_cache.find(*identity, cache_dir=cache_dir) if identity else None
    if entry is None:
        typer.echo("local mode: no cached hub settings; using built-in defaults "
                   "(private only, cloud disabled)", err=True)
        return LocalSettings(default_settings(), None, None)
    entry = settings_cache.mark_stale(entry)
    try:
        settings = OrganizationSettings.model_validate(entry.settings)
    except ValidationError:
        fail(f"cached settings at {entry.path} are invalid; run `resona settings pull`")
    typer.echo(f"local mode: cached settings revision {entry.revision} of organization "
               f"{entry.organization_id} (stale: not refreshed from the hub)", err=True)
    return LocalSettings(settings, entry.revision, entry.organization_id)


def _installed(candidate: Candidate, kind: str) -> bool:
    if kind == "document":
        return candidate.engine == "docling" and importlib.util.find_spec("resona_engine_docling") is not None
    return any(ep.name == candidate.engine for ep in entry_points(group="resona.engines"))


def _candidate_stage(candidate: Candidate, kind: str, settings: OrganizationSettings) -> Stage:
    if kind == "audio" and candidate.engine in _CLOUD_ASR_ENGINES:
        endpoint_id = f"cloud-asr:{candidate.engine}"
        registered = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
        endpoint = registered or Endpoint(id=endpoint_id, kind=EndpointKind.CLOUD_ASR, model=candidate.model)
        return Stage(name="asr", kind="asr", endpoint=endpoint)
    return Stage(name="asr" if kind == "audio" else "ocr", kind="asr" if kind == "audio" else "ocr")


def plan_local(
    local: LocalSettings,
    *,
    kind: str,
    execution_profile: Optional[str],
    engine: Optional[str],
    model: Optional[str],
    language: Optional[str],
    options: dict[str, Any],
    private: bool,
    profile: Optional[str],
) -> LocalPlan:
    """Resolve and policy-check everything before any processing (PolicyDenied escapes)."""
    from resona_postprocess.pipeline import build_pipeline, local_policy
    from resona_postprocess.profile import ProfileError, resolve_profile

    settings = local.settings
    if not private and not settings.cloud_enabled:
        raise PolicyDenied("policy", "cloud processing is disabled for this organization")
    policy = settings.policy(private=private)

    if kind == "text":
        # Postprocess-only: no engine stage; the profile is the whole job.
        if profile is None:
            raise LocalRunError("profile_required: text jobs need --profile")
        if execution_profile is not None or engine is not None or model is not None:
            raise LocalRunError("invalid_request: text jobs take no execution profile, engine or model")
        name, candidate = "", None
    else:
        name, runnable = _local_candidates(settings, policy, kind, execution_profile, engine, model, options)
        candidate = runnable[0]

    profile_ref = profile if profile is not None else (
        settings.postprocess_profile if settings.polish else None)
    pipeline = None
    if profile_ref is not None:
        try:
            prof = resolve_profile(profile_ref, _PROFILES_DIR)
        except ProfileError as e:
            raise LocalRunError(f"invalid_profile: {e}") from None
        pipeline = build_pipeline(prof, policy=local_policy(prof, base=policy))

    return LocalPlan(
        kind=kind,
        execution_profile=name,
        candidate=candidate,
        language=language or settings.language,
        options=options,
        private=private,
        pipeline=pipeline,
        postprocess_profile=profile_ref if isinstance(profile_ref, str) else None,
        settings_revision=local.revision,
    )


def _local_candidates(
    settings: OrganizationSettings,
    policy: Any,
    kind: str,
    execution_profile: Optional[str],
    engine: Optional[str],
    model: Optional[str],
    options: dict[str, Any],
) -> tuple[str, list[Candidate]]:
    """The execution profile's approved, policy-checked candidates installed on this machine."""
    name = execution_profile or settings.default_execution_profile
    exec_profile = settings.execution_profiles.get(name)
    if exec_profile is None:
        raise LocalRunError(f"execution_profile_not_found: no execution profile {name!r}")
    candidates = list(exec_profile.candidates(kind))  # type: ignore[arg-type]
    if not candidates:
        raise LocalRunError(f"no_candidates: execution profile {name!r} has no {kind} candidates")
    if engine is not None:
        candidates = [c for c in candidates if c.engine == engine]
    if model is not None:
        candidates = [c for c in candidates if c.model == model]
    if not candidates:
        raise LocalRunError("unapproved_override: engine/model override is not an approved candidate")
    ocr_engine = options.get("ocr_engine")
    if ocr_engine is not None:
        candidates = [c for c in candidates if c.ocr_engine == ocr_engine]
        if not candidates:
            raise LocalRunError("invalid_options: ocr_engine is not available in this execution profile")
    check_policy(policy, [_candidate_stage(c, kind, settings) for c in candidates])

    runnable = [c for c in candidates if _installed(c, kind)]
    if not runnable:
        names = ", ".join(f"{c.engine}/{c.model}@{c.accelerator}" for c in candidates)
        raise LocalRunError(f"no approved candidate of {name!r} is installed here ({names})")
    return name, runnable


def run_local(plan: LocalPlan, path: Path) -> JobResult:
    """Execute one file in a fresh execution subprocess (text runs read the file
    instead), then postprocess it."""
    result = _read_text(plan, path) if plan.candidate is None else _execute(plan, path)
    provenance = result.provenance.model_copy(update={
        "worker_id": "local", "settings_revision": plan.settings_revision})
    result = result.model_copy(update={"provenance": provenance})
    if plan.pipeline is None:
        return result
    from resona_postprocess.llm import PostprocessStepFailed

    try:
        run = plan.pipeline.run(result.markdown)
    except PostprocessStepFailed as e:
        raise LocalRunError(f"postprocess_failed: {e}") from None
    skipped = run.data.get("_skipped", [])
    return result.model_copy(update={
        "markdown": run.text,
        "raw_markdown": result.markdown if run.text != result.markdown else None,
        "warnings": [*result.warnings, *run.warnings, *(f"postprocess_step_skipped:{n}" for n in skipped)],
        "structured": {**result.structured, **{k: v for k, v in run.data.items() if k != "_skipped"}},
    })


def _read_text(plan: LocalPlan, path: Path) -> JobResult:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise LocalRunError(f"unsupported_input: {path.name} is not readable UTF-8 text ({e})") from None
    return JobResult(kind="text", raw_text=text, markdown=text, language=plan.language,
                     provenance=Provenance())


def _execute(plan: LocalPlan, path: Path) -> JobResult:
    if importlib.util.find_spec("resona_worker") is None:
        raise LocalRunError("local execution needs resona-worker; reinstall resona-cli")
    from resona_worker.execute import subprocess_env

    assert plan.candidate is not None
    with tempfile.TemporaryDirectory(prefix="resona-local-") as tmp:
        work = Path(tmp)
        snapshot = work / "snapshot.json"
        out = work / "result.json"
        snapshot.write_text(json.dumps({
            "kind": plan.kind,
            "candidate": plan.candidate.model_dump(mode="json"),
            "language": plan.language,
            "options": plan.options,
        }), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "resona_worker.execute",
             "--snapshot", str(snapshot), "--input", str(path.resolve()), "--out", str(out)],
            env=subprocess_env(private=plan.private, base=os.environ),
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0:
            raise LocalRunError(_EXIT_MESSAGES.get(proc.returncode, f"execution failed (exit {proc.returncode})"))
        return JobResult.model_validate_json(out.read_text(encoding="utf-8"))
