"""Assemble an evaluation from executed fixtures: metrics, gate, report and hub record.

Reports and hub records carry metrics and provenance only, never transcripts,
Markdown or other result text.

A run whose document jobs used a postprocess profile measures engine plus polish.
It is measurement-only: it is never gated against a baseline (baselines measure the
plain engines), its absolute checks still apply, and it cannot be promoted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from resona_job_core import JobResult

from .corpus import Corpus, Fixture
from .gate import TOLERANCE, Gate, evaluate_gate
from .metrics import MARKDOWN_NORMALIZATION, TEXT_NORMALIZATION
from .scoring import FixtureMetrics, score_fixture

REPORT_SCHEMA_VERSION = 1
MEASUREMENT_ONLY = "measurement_only"
PLATFORM_BY_ACCELERATOR = {"metal": "mac-metal", "cuda": "linux-cuda", "cpu": "cpu"}
_PRIMARY_STAGES = {"audio": ("asr",), "document": ("layout", "ocr")}


class EvaluationError(Exception):
    pass


@dataclass(frozen=True)
class FixtureRun:
    fixture: Fixture
    result: JobResult
    job_id: int | None = None


def measurement_only(postprocess_profile: str | None) -> bool:
    """Whether a run is measurement-only: never compared with, recorded as or promoted to a baseline.

    That holds for every run with a postprocess profile, because baselines measure the plain engines.
    """
    return postprocess_profile is not None


def primary_accelerator(result: JobResult) -> str:
    """Accelerator of the stage that defines the platform (ASR, or Docling layout)."""
    for name in _PRIMARY_STAGES[result.kind]:
        for stage in result.provenance.stages:
            if stage.stage == name:
                return stage.accelerator
    raise EvaluationError(f"{result.kind} result has no {'/'.join(_PRIMARY_STAGES[result.kind])} stage provenance")


def detect_platform(runs: Sequence[FixtureRun]) -> str:
    platforms = {PLATFORM_BY_ACCELERATOR.get(primary_accelerator(run.result), "?") for run in runs}
    if len(platforms) != 1 or "?" in platforms:
        raise EvaluationError(f"fixtures ran on mixed or unknown platforms {sorted(platforms)}; pass --platform")
    return platforms.pop()


def models_of(runs: Sequence[FixtureRun]) -> list[dict[str, Any]]:
    seen: dict[tuple, dict[str, Any]] = {}
    for run in runs:
        for stage in run.result.provenance.stages:
            key = (stage.stage, stage.engine, stage.model, stage.model_revision, stage.accelerator)
            seen[key] = {"stage": stage.stage, "engine": stage.engine, "model": stage.model,
                         "revision": stage.model_revision, "accelerator": stage.accelerator}
    return [seen[k] for k in sorted(seen, key=lambda k: tuple(str(v) for v in k))]


def score_runs(runs: Sequence[FixtureRun]) -> dict[str, FixtureMetrics]:
    return {run.fixture.id: score_fixture(run.fixture, run.result) for run in runs}


def build_report(
    *,
    corpus: Corpus,
    runs: Sequence[FixtureRun],
    execution_profile: str,
    platform: str,
    mode: str,
    release_id: str | None,
    baseline: Mapping[str, Any] | None,
    baseline_source: str | None,
    postprocess_profile: str | None = None,
) -> tuple[dict[str, Any], Gate]:
    """Score every run, apply the gate against ``baseline`` (already corpus-checked).

    With ``postprocess_profile`` the run is measurement-only: no baseline may be
    given, only the absolute checks gate it and the outcome is ``measurement_only``.
    """
    if {run.fixture.id for run in runs} != {f.id for f in corpus.fixtures}:
        raise EvaluationError("every corpus fixture must be evaluated exactly once")
    if measurement_only(postprocess_profile) and baseline is not None:
        raise EvaluationError("a postprocess run is not comparable with a plain baseline")
    metrics = score_runs(runs)
    gate = evaluate_gate(metrics, baseline["fixtures"] if baseline is not None else None)
    outcome = gate.outcome
    if measurement_only(postprocess_profile):
        gate = Gate(failures=gate.failures, baseline_required=False)
        outcome = gate.outcome if gate.failures else MEASUREMENT_ONLY
    runtimes = sorted({v for run in runs if (v := run.result.provenance.runtime_version)})
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": mode,
        "execution_profile": execution_profile,
        "platform": platform,
        "release_id": release_id,
        "postprocess_profile": postprocess_profile,
        "corpus_sha256": corpus.sha256,
        "normalization": {"audio": TEXT_NORMALIZATION, "document": MARKDOWN_NORMALIZATION},
        "tolerance": TOLERANCE,
        "runtime_versions": runtimes,
        "models": models_of(runs),
        "baseline": None if baseline is None else {
            "source": baseline_source,
            "revision": baseline.get("revision"),
            "evaluation_id": baseline.get("evaluation_id"),
            "corpus_sha256": baseline.get("corpus_sha256"),
            "release_id": baseline.get("release_id"),
        },
        "gate": {"outcome": outcome, "passed": gate.passed, "failures": list(gate.failures),
                 "baseline_required": gate.baseline_required},
        "evaluation_id": None,
        "fixtures": {
            run.fixture.id: {
                "kind": run.fixture.kind,
                "checks": list(run.fixture.checks),
                "job_id": run.job_id,
                "metrics": metrics[run.fixture.id],
                "provenance": {
                    "worker_id": run.result.provenance.worker_id,
                    "runtime_version": run.result.provenance.runtime_version,
                    "settings_revision": run.result.provenance.settings_revision,
                    "input_sha256": run.result.provenance.input_sha256,
                    "stages": [s.model_dump(mode="json") for s in run.result.provenance.stages],
                },
            }
            for run in runs
        },
    }
    return report, gate


def evaluation_record(report: Mapping[str, Any], gate: Gate) -> dict[str, Any]:
    """Body of ``POST /v1/evaluations``: identifiers, hashes and numbers only."""
    if measurement_only(report.get("postprocess_profile")):
        raise EvaluationError("a postprocess run is measurement-only and is not recorded as an evaluation")
    baseline = report["baseline"]
    return {
        "profile": report["execution_profile"],
        "execution_profile": report["execution_profile"],
        "platform": report["platform"],
        "release_id": report["release_id"],
        "corpus_sha256": report["corpus_sha256"],
        "baseline_revision": baseline["revision"] if baseline is not None else None,
        "fixtures": {fid: dict(entry["metrics"]) for fid, entry in report["fixtures"].items()},
        "fixture_checks": {
            fid: {"kind": entry["kind"], "checks": list(entry["checks"])}
            for fid, entry in report["fixtures"].items()
        },
        "gate": gate.record(),
    }
