"""``resona-worker prepare``: prove which candidate tuples this machine can serve.

Pins come from ``--release-manifest`` or, by default, from the installed release's
packaged ``models.json``; prepare never accepts an unpinned snapshot. For every
candidate of one execution profile the worker:

1. makes every model it loads, including Docling's layout and table-structure
   weights, resolve to the pinned revision in the local caches, downloading
   missing pinned weights unless ``offline`` (this happens for all candidates
   before the first synthetic run);
2. runs a synthetic job through the real execution subprocess, with the same
   private, offline, credential-free environment production jobs get;
3. checks the output for known content, checks that every HuggingFace model the run
   recorded in its provenance is the pinned revision, and records seconds, peak RSS,
   peak accelerator memory and the accelerator each stage actually used.

The manifest is written only when every candidate passes, so a failed prepare
keeps the previous working file. ``run`` reports and claims exactly the tested
tuples; the hub additionally intersects them with the tuples it approved.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from resona_asr_core.model_cache import configure_model_cache
from resona_job_core import Candidate, JobResult, OrganizationSettings

from .config import CapabilitiesManifest, PreparedCapability, WeightRecord, runtime_version
from .execute import ExitCode, subprocess_env
from .weights import (
    WeightsError,
    download_whisper_checkpoint,
    ensure_pinned_snapshot,
    is_hub_repo,
    is_whisper_alias,
    release_model_pins,
    sha256_file,
    whisper_checkpoint,
    whisper_revision,
)

DEFAULT_TIMEOUT_SECONDS = 900.0


class PrepareError(RuntimeError):
    """One candidate could not be prepared. Messages carry no content."""


@dataclass(frozen=True)
class _Synthetic:
    fixture: str
    language: str
    options: dict[str, Any]
    expected: tuple[str, ...]


# Synthetic, non-sensitive fixtures shipped with the package (lower-cased expectations).
_SYNTHETIC = {
    "audio": _Synthetic(fixture="de_short.wav", language="de", options={}, expected=("guten tag", "test")),
    "document": _Synthetic(
        fixture="scan_de.pdf",
        language="de",
        options={"ocr": "force"},
        expected=("synthetisches testdokument", "brücke"),
    ),
}


def profile_candidates(settings: OrganizationSettings, profile: str) -> list[tuple[str, Candidate]]:
    """(kind, candidate) pairs of one execution profile, in profile order."""
    try:
        execution_profile = settings.execution_profiles[profile]
    except KeyError:
        raise PrepareError(f"execution profile {profile!r} is not defined in the settings") from None
    pairs = [("audio", c) for c in execution_profile.audio] + [("document", c) for c in execution_profile.document]
    if not pairs:
        raise PrepareError(f"execution profile {profile!r} has no candidates")
    return pairs


def _pin(model_pins: Mapping[str, str], model: str) -> str:
    pinned = model_pins.get(model)
    if pinned is None:
        raise PrepareError(f"the release does not pin a revision for {model}")
    return pinned


def _required_revision(candidate: Candidate, model_pins: Mapping[str, str]) -> str:
    """The release's pin for the candidate model; a settings pin must agree with it."""
    pinned = _pin(model_pins, candidate.model)
    if candidate.model_revision and candidate.model_revision != pinned:
        raise PrepareError(
            f"settings pin {candidate.model} at {candidate.model_revision}, the release pins {pinned}"
        )
    return pinned


def _stage_weights(kind: str) -> list[tuple[str, str | None]]:
    """HuggingFace weights an engine loads beyond the candidate model (Docling layout/table models)."""
    if kind != "document":
        return []
    try:
        from resona_engine_docling import stage_weights
    except ImportError:
        raise PrepareError("Docling is not installed in this environment") from None
    return stage_weights()


def pin_weights(
    kind: str, candidate: Candidate, model_pins: Mapping[str, str], *, offline: bool = False
) -> list[WeightRecord]:
    """Make every model ``candidate`` loads resolve to its pinned revision; return what was pinned.

    Missing pinned weights are downloaded (network; preparation only) unless ``offline``.
    """
    records: list[WeightRecord] = []
    try:
        if is_whisper_alias(candidate.engine, candidate.model):
            records.append(_pin_whisper_checkpoint(candidate, model_pins, offline=offline))
        elif is_hub_repo(candidate.model):
            pinned = _required_revision(candidate, model_pins)
            ensure_pinned_snapshot(candidate.model, candidate.model_revision, pinned, offline=offline)
            records.append(WeightRecord(repo=candidate.model, revision=pinned))
        for repo, ref in _stage_weights(kind):
            pinned = _pin(model_pins, repo)
            ensure_pinned_snapshot(repo, ref, pinned, offline=offline)
            records.append(WeightRecord(repo=repo, revision=pinned))
    except WeightsError as exc:
        raise PrepareError(str(exc)) from None
    return records


def _pin_whisper_checkpoint(candidate: Candidate, model_pins: Mapping[str, str], *, offline: bool) -> WeightRecord:
    """openai-whisper alias: the checkpoint must exist and hash to the published and pinned SHA-256."""
    published = whisper_revision(candidate.model)
    required = _required_revision(candidate, model_pins)
    if required != published:
        raise PrepareError(
            f"pinned revision of whisper {candidate.model} is {required}, the installed openai-whisper publishes {published}"
        )
    checkpoint = whisper_checkpoint(candidate.model)
    if checkpoint is None:
        if offline:
            raise PrepareError(
                f"whisper checkpoint {candidate.model}@{published} is not prepared and --offline forbids downloading it"
            )
        checkpoint = download_whisper_checkpoint(candidate.model)
    if sha256_file(checkpoint) != published:
        raise PrepareError(f"whisper checkpoint {candidate.model} does not match its published SHA-256")
    return WeightRecord(repo=candidate.model, revision=published)


def _check_recorded_revisions(kind: str, result: JobResult, model_pins: Mapping[str, str]) -> None:
    """Every HuggingFace model the synthetic run recorded must be the release's pinned revision."""
    for stage in result.provenance.stages:
        if not is_hub_repo(stage.model):
            continue
        pinned = model_pins.get(stage.model)
        if pinned is None:
            raise PrepareError(
                f"the {kind} run loaded {stage.model} ({stage.engine}), which the release does not pin"
            )
        if stage.model_revision != pinned:
            raise PrepareError(
                f"the {kind} run loaded {stage.model}@{stage.model_revision} ({stage.engine}), the release pins {pinned}"
            )


def _peak_rss_bytes(usage: resource.struct_rusage) -> int:
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    return int(usage.ru_maxrss) if sys.platform == "darwin" else int(usage.ru_maxrss) * 1024


@dataclass(frozen=True)
class _Run:
    exit_code: int
    seconds: float
    peak_rss_bytes: int
    result: JobResult | None
    peak_accelerator_bytes: int | None


def _execute(
    executor: Sequence[str], snapshot: dict[str, Any], input_path: Path, scratch: Path, timeout: float
) -> _Run:
    snapshot_path = scratch / "snapshot.json"
    out_path = scratch / "out.json"
    metrics_path = scratch / "metrics.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    cmd = [
        *executor,
        "--snapshot", str(snapshot_path),
        "--input", str(input_path),
        "--out", str(out_path),
        "--metrics", str(metrics_path),
    ]
    started = time.perf_counter()
    with (scratch / "engine.stderr").open("wb") as stderr:
        proc = subprocess.Popen(
            cmd,
            env=subprocess_env(private=True, base=os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            start_new_session=True,
        )
    timer = threading.Timer(timeout, lambda: _kill(proc.pid))
    timer.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    finally:
        timer.cancel()
    proc.returncode = os.waitstatus_to_exitcode(status)
    seconds = time.perf_counter() - started

    result = None
    accelerator_bytes = None
    if proc.returncode == ExitCode.OK:
        try:
            result = JobResult.model_validate_json(out_path.read_bytes())
            accelerator_bytes = json.loads(metrics_path.read_text(encoding="utf-8")).get("peak_accelerator_bytes")
        except (OSError, ValueError, ValidationError):
            result = None
    return _Run(proc.returncode, seconds, _peak_rss_bytes(usage), result, accelerator_bytes)


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, 9)
    except ProcessLookupError:
        pass


def _check_result(kind: str, candidate: Candidate, run: _Run) -> JobResult:
    if run.exit_code != ExitCode.OK:
        name = ExitCode(run.exit_code).name.lower() if run.exit_code in set(ExitCode) else f"exit {run.exit_code}"
        raise PrepareError(f"synthetic {kind} run of {candidate.engine}/{candidate.model} failed: {name}")
    result = run.result
    if result is None or result.kind != kind:
        raise PrepareError(f"synthetic {kind} run of {candidate.engine}/{candidate.model} wrote no valid result")
    text = f"{result.raw_text}\n{result.markdown}".lower()
    missing = [phrase for phrase in _SYNTHETIC[kind].expected if phrase not in text]
    if missing:
        raise PrepareError(
            f"synthetic {kind} run of {candidate.engine}/{candidate.model} missed expected content ({len(missing)} phrase(s))"
        )
    primary = "asr" if kind == "audio" else "layout"
    if not any(s.stage == primary and s.accelerator == candidate.accelerator for s in result.provenance.stages):
        raise PrepareError(
            f"synthetic {kind} run of {candidate.engine}/{candidate.model} did not run {primary} on {candidate.accelerator}"
        )
    return result


def prepare_candidate(
    kind: str,
    candidate: Candidate,
    *,
    executor: Sequence[str],
    model_pins: Mapping[str, str],
    weights: Sequence[WeightRecord],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PreparedCapability:
    """One synthetic run of a candidate whose ``weights`` :func:`pin_weights` already pinned."""
    synthetic = _SYNTHETIC[kind]
    snapshot = {
        "kind": kind,
        "candidate": candidate.model_dump(mode="json"),
        "language": synthetic.language,
        "options": synthetic.options,
    }
    with tempfile.TemporaryDirectory(prefix="resona-prepare-") as tmp, as_file(
        files("resona_worker") / "fixtures" / synthetic.fixture
    ) as fixture:
        scratch = Path(tmp)
        input_path = scratch / f"input{fixture.suffix}"
        input_path.write_bytes(fixture.read_bytes())
        run = _execute(executor, snapshot, input_path, scratch, timeout)
    result = _check_result(kind, candidate, run)
    _check_recorded_revisions(kind, result, model_pins)

    recorded = list(weights)
    for stage in result.provenance.stages:
        if is_hub_repo(stage.model) and stage.model_revision:
            record = WeightRecord(repo=stage.model, revision=stage.model_revision)
            if record not in recorded:
                recorded.append(record)
    accelerators = {f"{stage.stage}:{stage.engine}": stage.accelerator for stage in result.provenance.stages}
    return PreparedCapability(
        kind=kind,  # type: ignore[arg-type]
        candidate=candidate,
        weights=recorded,
        seconds=round(run.seconds, 3),
        peak_rss_bytes=run.peak_rss_bytes,
        peak_accelerator_bytes=run.peak_accelerator_bytes,
        stage_accelerators=accelerators,
    )


def prepare(
    settings: OrganizationSettings,
    profile: str,
    *,
    executor: Sequence[str] = (sys.executable, "-m", "resona_worker.execute"),
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    on_tested: Callable[[PreparedCapability], None] | None = None,
    model_pins: Mapping[str, str] | None = None,
    offline: bool = False,
) -> CapabilitiesManifest:
    """Prepare every candidate of ``profile``; raise ``PrepareError`` on the first failure.

    ``model_pins`` (``model -> revision``) come from a release manifest; ``None`` means the
    installed release's packaged pins. Every model a candidate loads must be pinned, and all
    of them are pinned in the local caches (downloaded unless ``offline``) before the first
    synthetic run.
    """
    pairs = profile_candidates(settings, profile)
    if model_pins is None:
        try:
            model_pins = release_model_pins()
        except WeightsError as exc:
            raise PrepareError(str(exc)) from None
    # The execution subprocess honours RESONA_MODELS_DIR the same way.
    configure_model_cache()
    weights = [pin_weights(kind, candidate, model_pins, offline=offline) for kind, candidate in pairs]
    tested = []
    for (kind, candidate), pinned in zip(pairs, weights, strict=True):
        item = prepare_candidate(
            kind, candidate, executor=executor, timeout=timeout, model_pins=model_pins, weights=pinned
        )
        tested.append(item)
        if on_tested is not None:
            on_tested(item)
    return CapabilitiesManifest(
        profile=profile,
        prepared_at=datetime.now(UTC),
        runtime_version=runtime_version(),
        platform=f"{sys.platform}-{platform.machine()}",
        tested=tested,
    )


def write_manifest(manifest: CapabilitiesManifest, path: Path) -> None:
    """Atomically replace ``path`` with the manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(manifest.model_dump_json(indent=2))
            handle.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "PrepareError",
    "pin_weights",
    "prepare",
    "prepare_candidate",
    "profile_candidates",
    "write_manifest",
]
