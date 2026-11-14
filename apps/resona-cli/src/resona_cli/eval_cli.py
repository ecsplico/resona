"""``resona eval``: run the evaluation corpus through an execution profile and gate it.

``resona eval --profile <name> --output-dir <dir>`` runs every fixture of
``eval/corpus`` locally through the explicit ``--local`` path (:mod:`.local_run`,
same execution subprocess) and compares against ``eval/baselines.json``. With
``--remote`` each fixture becomes a ``purpose=evaluation`` hub job (org-admin
credential, staged release), the evaluation summary is recorded on the hub
(``POST /v1/evaluations``) and compared against the hub's ``GET /v1/baselines``.
``report.json`` holds metrics and provenance only, never transcripts.

``resona eval promote <id> --report <dir>/report.json`` promotes a recorded
evaluation on the hub and pins it in ``eval/baselines.json``.

Fixture ``options`` from the manifest (document ``ocr``…, audio ``diarize``) are merged
into each job's default options. ``--postprocess-profile <name>`` (``--remote`` only) runs the document jobs
with that hub postprocess profile; such a run is measurement-only: it is never
compared with a baseline, not recorded on the hub and cannot be promoted, because
baselines measure the plain engines.

Exit codes: 0 gate passed, 1 error, 2 policy denied, 3 gate failed,
4 ``baseline_required`` (no comparable baseline; promotable when absolute checks pass).
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import typer
from resona_client.client import (
    PolicyDeniedError,
    ResonaAPIError,
    ResonaClient,
)
from resona_eval import (
    BaselineFileError,
    CorpusError,
    EvaluationError,
    FixtureRun,
    ScoringError,
    build_report,
    comparable,
    detect_platform,
    evaluation_record,
    find_baseline,
    load_corpus,
    measurement_only,
    promoted_entry,
    upsert_baseline,
)
from resona_eval.corpus import Corpus, Fixture
from resona_job_core import PolicyDenied

from .hub import connect_hub, exit_policy_denied, fail

EXIT_GATE_FAILED = 3
EXIT_BASELINE_REQUIRED = 4
PLATFORMS = ("mac-metal", "linux-cuda", "cpu")

eval_app = typer.Typer(help="Evaluate an execution profile against the corpus and baselines.")


def _fixture_options(fixture: Fixture) -> dict[str, Any]:
    """The options a normal job gets by default (``vad_filter`` on), so eval measures production.

    Fixtures merge their manifest ``options`` (e.g. ``ocr=force``, ``diarize``) over the defaults.
    """
    if fixture.kind == "audio":
        return fixture.audio_options().model_dump()
    return fixture.document_options().model_dump(exclude_none=True)


def _run_local(corpus: Corpus, profile: str) -> list[FixtureRun]:
    from .local_run import LocalRunError, load_local_settings, plan_local, run_local

    local = load_local_settings()
    runs = []
    try:
        for fixture in corpus.fixtures:
            plan = plan_local(
                local, kind=fixture.kind, execution_profile=profile, engine=None, model=None,
                language=fixture.language, options=_fixture_options(fixture), private=True, profile=None,
            )
            # The corpus measures the engines: organization polish never runs here.
            plan = dataclasses.replace(plan, pipeline=None, postprocess_profile=None)
            typer.echo(f"eval: {fixture.id} locally …", err=True)
            runs.append(FixtureRun(fixture=fixture, result=run_local(plan, corpus.path(fixture))))
    except PolicyDenied as e:
        exit_policy_denied(f"{e.stage}: {e.reason}")
    except LocalRunError as e:
        fail(str(e))
    return runs


def _run_remote(client: ResonaClient, corpus: Corpus, profile: str, release_id: str,
                timeout: float, postprocess_profile: Optional[str] = None) -> list[FixtureRun]:
    submitted = []
    for fixture in corpus.fixtures:
        options = _fixture_options(fixture)
        if options.get("diarize") is False:
            # Evaluation jobs never take the profile's diarize default on the hub, so a false
            # flag says nothing there; leaving it out keeps hubs predating diarize working.
            del options["diarize"]
        ref = client.submit_job(
            corpus.path(fixture), kind=fixture.kind, execution_profile=profile,
            language=fixture.language, options=options, private=True,
            profile=postprocess_profile if fixture.kind == "document" else None,
            purpose="evaluation", release_id=release_id,
            provenance={"source": f"resona-eval:{corpus.sha256[:12]}:{fixture.id}"},
        )
        typer.echo(f"eval: {fixture.id} → job {ref.job_id}", err=True)
        submitted.append((fixture, ref.job_id))
    runs = []
    for fixture, job_id in submitted:
        job = client.wait_for_job(job_id, timeout=timeout)
        if job.status != "completed":
            code = (job.error or {}).get("code", job.status)
            fail(f"evaluation job {job_id} ({fixture.id}) ended {job.status}: {code}")
        runs.append(FixtureRun(fixture=fixture, result=client.get_result(job_id), job_id=job_id))
    return runs


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _summary(report: dict[str, Any]) -> None:
    typer.echo(f"eval: {report['execution_profile']} on {report['platform']} "
               f"corpus {report['corpus_sha256'][:12]} ({report['mode']})", err=True)
    if measurement_only(report["postprocess_profile"]):
        typer.echo(f"postprocess profile {report['postprocess_profile']} on document fixtures: measurement only, "
                   "not compared with or promotable as a baseline", err=True)
    for fid, entry in report["fixtures"].items():
        metrics = " ".join(f"{k}={v}" for k, v in entry["metrics"].items())
        typer.echo(f"  {fid}: {metrics}", err=True)
    gate = report["gate"]
    for failure in gate["failures"]:
        typer.echo(f"  gate failure: {failure}", err=True)
    if gate["baseline_required"]:
        typer.echo(f"baseline_required: no promoted baseline for {report['execution_profile']}/"
                   f"{report['platform']} with this corpus", err=True)
    typer.echo(f"gate: {gate['outcome']}", err=True)


@eval_app.callback(invoke_without_command=True)
def evaluate(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(None, "--profile", help="Execution profile to evaluate (e.g. cpu-private)."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory for report.json."),
    remote: bool = typer.Option(False, "--remote", help="Run fixtures as hub evaluation jobs (org-admin key)."),
    platform: Optional[str] = typer.Option(None, "--platform", help="Expected platform: mac-metal, linux-cuda or cpu "
                                           "(default: detected from the stage provenance)."),
    release_id: Optional[str] = typer.Option(None, "--release-id", help="Staged release to evaluate "
                                             "(--remote; default: the hub's /v1/version release)."),
    corpus_dir: Path = typer.Option(Path("eval/corpus"), "--corpus", help="Corpus directory with MANIFEST.json."),
    baselines: Path = typer.Option(Path("eval/baselines.json"), "--baselines", help="Pinned baselines file (local mode)."),
    timeout: float = typer.Option(1800.0, "--timeout", help="Seconds to wait for each hub job."),
    postprocess_profile: Optional[str] = typer.Option(
        None, "--postprocess-profile",
        help="Hub postprocess profile for the document jobs (--remote; measurement only, never a baseline).",
    ),
):
    """Evaluate an execution profile; exit 0 pass, 3 gate failed, 4 baseline_required."""
    if ctx.invoked_subcommand is not None:
        return
    if profile is None or output_dir is None:
        fail("--profile and --output-dir are required")
    if platform is not None and platform not in PLATFORMS:
        fail(f"--platform must be one of {', '.join(PLATFORMS)}")
    if postprocess_profile is not None and not remote:
        fail("--postprocess-profile needs --remote: local mode cannot resolve hub postprocess profiles")
    try:
        corpus = load_corpus(corpus_dir)
    except CorpusError as e:
        fail(f"corpus: {e}")

    client = None
    hub_baseline_revision = None
    try:
        if remote:
            client = connect_hub()
            release_id = release_id or str(client.get_version()["release_id"])
            runs = _run_remote(client, corpus, profile, release_id, timeout, postprocess_profile)
        else:
            if release_id is not None:
                fail("--release-id needs --remote")
            runs = _run_local(corpus, profile)
        detected = detect_platform(runs)
        if platform is not None and platform != detected:
            fail(f"fixtures ran on {detected}, not {platform}")
        if measurement_only(postprocess_profile):
            baseline, source = None, None  # baselines measure the plain engines
        elif client is not None:
            hub_baseline = client.get_baseline(execution_profile=profile, platform=detected)
            hub_baseline_revision = hub_baseline["revision"] if hub_baseline else None
            baseline, source = comparable(hub_baseline, corpus.sha256), "hub"
        else:
            baseline, source = comparable(find_baseline(baselines, profile, detected), corpus.sha256), str(baselines)
        report, gate = build_report(
            corpus=corpus, runs=runs, execution_profile=profile, platform=detected,
            mode="remote" if remote else "local", release_id=release_id,
            baseline=baseline, baseline_source=source if baseline is not None else None,
            postprocess_profile=postprocess_profile,
        )
        if client is not None and not measurement_only(postprocess_profile):
            report["hub_baseline_revision"] = hub_baseline_revision
            record = client.create_evaluation(evaluation_record(report, gate))
            report["evaluation_id"] = record["evaluation_id"]
    except PolicyDeniedError as e:
        exit_policy_denied(e.message)
    except ResonaAPIError as e:
        fail(f"hub: {e}")
    except (EvaluationError, ScoringError, BaselineFileError) as e:
        fail(str(e))
    finally:
        if client is not None:
            client.close()

    report_path = output_dir / "report.json"
    _write_json(report_path, report)
    _summary(report)
    if report["evaluation_id"] is not None:
        typer.echo(f"evaluation {report['evaluation_id']} recorded; promote with "
                   f"`resona eval promote {report['evaluation_id']} --report {report_path}`", err=True)
    typer.echo(str(report_path))
    if gate.failures:
        raise typer.Exit(EXIT_GATE_FAILED)
    if gate.baseline_required:
        raise typer.Exit(EXIT_BASELINE_REQUIRED)


@eval_app.command("promote")
def promote(
    evaluation_id: int = typer.Argument(..., help="Evaluation recorded by `resona eval --remote`."),
    report_path: Path = typer.Option(..., "--report", help="report.json written by that evaluation."),
    baselines: Path = typer.Option(Path("eval/baselines.json"), "--baselines", help="Pinned baselines file to update."),
    rebaseline: bool = typer.Option(
        False, "--rebaseline",
        help="Start a new baseline on a changed corpus (absolute checks only, no regression comparison).",
    ),
):
    """Promote a recorded evaluation to the hub baseline and pin it in eval/baselines.json."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"cannot read report {report_path}: {e}")
    if measurement_only(report.get("postprocess_profile")):
        fail(f"{report_path} measured postprocess profile {report['postprocess_profile']!r}; "
             "postprocess runs cannot become a baseline")
    if report.get("mode") != "remote" or report.get("evaluation_id") != evaluation_id:
        fail(f"{report_path} is not the report of hub evaluation {evaluation_id}")
    client = connect_hub()
    try:
        promoted = client.promote_evaluation(
            evaluation_id, expected_baseline_revision=report.get("hub_baseline_revision"), rebaseline=rebaseline)
    except PolicyDeniedError as e:
        exit_policy_denied(e.message)
    except ResonaAPIError as e:
        fail(f"hub: {e}")
    finally:
        client.close()
    entry = promoted_entry(report, evaluation_id=evaluation_id, revision=int(promoted["baseline_revision"]),
                           promoted_at=datetime.now(UTC).isoformat(timespec="seconds"))
    try:
        upsert_baseline(baselines, entry)
    except BaselineFileError as e:
        fail(str(e))
    typer.echo(f"promoted evaluation {evaluation_id}: {entry['execution_profile']}/{entry['platform']} "
               f"baseline revision {entry['revision']} → {baselines}", err=True)
