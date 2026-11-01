"""Shared engine of ``resona transcribe`` and ``resona doc``.

Hub mode uploads each file as an audio/document job, then (unless ``--no-wait``)
waits and writes ``<stem>.md`` + ``<stem>.result.json``. ``--local`` runs the same
policy path on this machine (:mod:`.local_run`). There is no automatic fallback
between the two: an unreachable hub, a timeout or a policy denial is an error.

Exit codes: 0 success, 1 error or failed job, 2 policy denial or colliding output names.
"""
from __future__ import annotations

import itertools
import json
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import httpx
import typer
from resona_client.client import (
    AudioOptions,
    DocumentOptions,
    JobTimeout,
    JobView,
    PolicyDeniedError,
    ResonaAPIError,
    ResonaClient,
)
from resona_job_core import PolicyDenied

from .hub import EXIT_ERROR, EXIT_POLICY_DENIED, candidate_label, connect_hub, policy_summary
from .outputs import OutputExists, output_paths, result_metadata, write_result

Options = Union[AudioOptions, DocumentOptions]
EXIT_OUTPUT_COLLISION = 2


@dataclass
class MediaRequest:
    kind: str
    files: list[Path]
    output_dir: Optional[Path]
    force: bool
    as_json: bool
    no_wait: bool
    wait_timeout: Optional[float]
    private: bool
    execution_profile: Optional[str]
    language: Optional[str]
    engine: Optional[str]
    model: Optional[str]
    profile: Optional[str]
    options: Options
    local: bool


@dataclass
class _Run:
    """Per-file outcomes; rendered as JSON on stdout with ``--json``."""

    as_json: bool
    records: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0

    def add(self, source: Path, **values: Any) -> dict[str, Any]:
        record = {"input": str(source), **values}
        self.records.append(record)
        return record

    def failed(self, code: int = EXIT_ERROR) -> None:
        self.exit_code = max(self.exit_code, code)

    def finish(self) -> None:
        if self.as_json:
            typer.echo(json.dumps({"jobs": self.records}, ensure_ascii=False, indent=2))
        if self.exit_code:
            raise typer.Exit(self.exit_code)


@contextmanager
def _spinner(label: str):
    if not sys.stderr.isatty():
        yield
        return
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    stop = threading.Event()

    def _spin():
        for frame in itertools.cycle(frames):
            if stop.is_set():
                break
            sys.stderr.write(f"\r{frame} {label}")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(label) + 2) + "\r")
        sys.stderr.flush()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()


def hub_profile_name(profile: Optional[str]) -> Optional[str]:
    """A hub postprocess profile is a stored name; files and inline JSON are local-mode only."""
    if profile is None:
        return None
    path = Path(profile)
    if profile.lstrip().startswith("{") or (path.suffix == ".json" and path.exists()):
        typer.echo(
            "Error: with the hub, --profile takes the name of a profile stored on the hub; "
            "profile files and inline JSON are only supported with --local.",
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)
    return profile


def _say(run: _Run, message: str) -> None:
    """Human progress goes to stdout unless --json owns stdout, then stderr."""
    typer.echo(message, err=run.as_json)


def _admission_error(e: ResonaAPIError) -> str:
    if e.code == "unapproved_override":
        return ("unapproved_override: the --engine/--model override is not an approved candidate "
                "of this execution profile (see `resona settings get`)")
    return f"{e.code or e.status}: {e.message}"


def _reject_colliding_outputs(req: MediaRequest) -> None:
    """Compute every input's ``<stem>.md``/``<stem>.result.json`` before any work starts.

    Two inputs with the same stem (``a.mp3`` and ``a.wav``, or one file given twice)
    would write the same files; reject the whole run with exit 2 rather than let the
    second result overwrite or fail after both jobs ran. Names are compared
    case-insensitively because the default macOS filesystem is.
    """
    seen: dict[str, Path] = {}
    for source in req.files:
        markdown = output_paths(source, req.output_dir).markdown
        key = str(markdown.resolve()).casefold()
        if key in seen:
            typer.echo(
                f"Error: {seen[key].name} and {source.name} would both write {markdown} "
                f"(stem {source.stem!r}); nothing was started. Rename one input or process them "
                "into different --output-dir directories.",
                err=True,
            )
            raise typer.Exit(EXIT_OUTPUT_COLLISION)
        seen[key] = source


def run_media(req: MediaRequest) -> None:
    if req.local:
        if req.no_wait:
            typer.echo("Error: --no-wait needs the hub; --local runs synchronously", err=True)
            raise typer.Exit(EXIT_ERROR)
        _run_local(req)
    else:
        _run_hub(req)


# -- Hub ----------------------------------------------------------------------

def _run_hub(req: MediaRequest) -> None:
    profile = hub_profile_name(req.profile)
    if not req.no_wait:
        _reject_colliding_outputs(req)
    client = connect_hub()
    run = _Run(as_json=req.as_json)
    submitted: list[tuple[Path, int]] = []
    try:
        for source in req.files:
            paths = output_paths(source, req.output_dir)
            if not req.no_wait and not req.force and paths.existing():
                message = f"{paths.existing()[0]} exists (use --force to overwrite)"
                typer.echo(f"Skipped {source.name}: {message}", err=True)
                run.add(source, status="skipped", error={"code": "output_exists", "message": message})
                run.failed()
                continue
            ref = client.submit_job(
                source,
                kind=req.kind,  # type: ignore[arg-type]
                execution_profile=req.execution_profile,
                language=req.language,
                engine=req.engine,
                model=req.model,
                profile=profile,
                options=req.options,
                private=req.private,
            )
            submitted.append((source, ref.job_id))
    except PolicyDeniedError as e:
        typer.echo(f"policy_denied: {e.message}", err=True)
        _abort(run, submitted, EXIT_POLICY_DENIED)
    except ResonaAPIError as e:
        typer.echo(f"Error: {_admission_error(e)}", err=True)
        _abort(run, submitted, EXIT_ERROR)
    except (httpx.HTTPError, OSError) as e:
        typer.echo(f"Error: hub request failed: {e}", err=True)
        _abort(run, submitted, EXIT_ERROR)

    try:
        if req.no_wait:
            for source, job_id in submitted:
                job = client.get_job(job_id)
                typer.echo(policy_summary(job), err=True)
                run.add(source, **_job_fields(job))
                if not req.as_json:
                    typer.echo(str(job_id))
        else:
            for source, job_id in submitted:
                _wait_and_write(client, req, run, source, job_id, profile)
    except (httpx.HTTPError, ResonaAPIError) as e:
        typer.echo(f"Error: hub request failed: {e}", err=True)
        run.failed()
    run.finish()


def _abort(run: _Run, submitted: list[tuple[Path, int]], code: int) -> None:
    """Stop submitting; jobs already accepted keep running and are reported."""
    for source, job_id in submitted:
        typer.echo(f"job {job_id} ({source.name}) was already submitted and keeps running", err=True)
        run.add(source, job_id=job_id, status="submitted")
    run.failed(code)
    run.finish()


def _job_fields(job: JobView) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "private": job.private,
        "execution_profile": job.execution_profile,
        "candidate": job.candidate,
        "queue_reason": job.queue_reason,
        "error": job.error,
    }


def _wait_and_write(client: ResonaClient, req: MediaRequest, run: _Run, source: Path,
                    job_id: int, profile: Optional[str]) -> None:
    try:
        with _spinner(f"Processing {source.name} (job {job_id})…"):
            job = client.wait_for_job(job_id, timeout=req.wait_timeout)
    except JobTimeout:
        message = (f"job {job_id} is still running on the hub; "
                   f"continue with `resona jobs wait {job_id}`")
        typer.echo(f"Timeout: {message}", err=True)
        run.add(source, job_id=job_id, status="timeout", error={"code": "timeout", "message": message})
        run.failed()
        return
    typer.echo(policy_summary(job), err=True)
    record = run.add(source, **_job_fields(job))
    if job.status != "completed":
        error = job.error or {}
        message = error.get("message") or job.status
        typer.echo(f"Failed {source.name}: job {job_id} {job.status}: {message}", err=True)
        run.failed(EXIT_POLICY_DENIED if error.get("code") == "policy_denied" else EXIT_ERROR)
        return
    result = client.get_result(job_id)
    paths = output_paths(source, req.output_dir)
    meta = result_metadata(source, result, job_id=job_id, private=job.private,
                           execution_profile=job.execution_profile, postprocess_profile=profile)
    try:
        write_result(paths, result, meta, force=req.force)
    except OutputExists as e:
        typer.echo(f"Error: {e}", err=True)
        record["error"] = {"code": "output_exists", "message": str(e)}
        run.failed()
        return
    record.update(markdown=str(paths.markdown), result=str(paths.result))
    _say(run, f"{source.name} -> {paths.markdown}")


# -- Local --------------------------------------------------------------------

def _run_local(req: MediaRequest) -> None:
    from .local_run import LocalRunError, load_local_settings, plan_local, run_local

    _reject_colliding_outputs(req)
    run = _Run(as_json=req.as_json)
    local = load_local_settings()
    try:
        plan = plan_local(
            local,
            kind=req.kind,
            execution_profile=req.execution_profile,
            engine=req.engine,
            model=req.model,
            language=req.language,
            options=req.options.model_dump(exclude_none=True),
            private=req.private,
            profile=req.profile,
        )
    except PolicyDenied as e:
        typer.echo(f"policy_denied: {e.stage}: {e.reason}", err=True)
        run.failed(EXIT_POLICY_DENIED)
        run.finish()
    except LocalRunError as e:
        typer.echo(f"Error: {e}", err=True)
        run.failed()
        run.finish()

    private = "true" if plan.private else "false (cloud allowed)"
    typer.echo(f"local: private={private} execution_profile={plan.execution_profile} "
               f"candidate={candidate_label(plan.candidate.model_dump())}", err=True)
    fields = {"kind": plan.kind, "private": plan.private,
              "execution_profile": plan.execution_profile,
              "candidate": plan.candidate.model_dump(mode="json")}
    for source in req.files:
        paths = output_paths(source, req.output_dir)
        if not req.force and paths.existing():
            message = f"{paths.existing()[0]} exists (use --force to overwrite)"
            typer.echo(f"Skipped {source.name}: {message}", err=True)
            run.add(source, status="skipped", error={"code": "output_exists", "message": message}, **fields)
            run.failed()
            continue
        try:
            with _spinner(f"Processing {source.name} locally…"):
                result = run_local(plan, source)
            meta = result_metadata(source, result, job_id=None, private=plan.private,
                                   execution_profile=plan.execution_profile,
                                   postprocess_profile=plan.postprocess_profile)
            write_result(paths, result, meta, force=req.force)
        except PolicyDenied as e:
            typer.echo(f"policy_denied: {e.stage}: {e.reason}", err=True)
            run.add(source, status="failed", error={"code": "policy_denied", "message": str(e)}, **fields)
            run.failed(EXIT_POLICY_DENIED)
            break
        except (LocalRunError, OutputExists) as e:
            typer.echo(f"Failed {source.name}: {e}", err=True)
            run.add(source, status="failed", error={"code": "local_failed", "message": str(e)}, **fields)
            run.failed()
            continue
        run.add(source, status="completed", markdown=str(paths.markdown),
                result=str(paths.result), **fields)
        _say(run, f"{source.name} -> {paths.markdown}")
    run.finish()
