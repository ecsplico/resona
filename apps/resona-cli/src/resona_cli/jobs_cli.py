"""``resona jobs``: inspect and manage the organization's hub jobs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import httpx
import typer
from resona_client.client import (
    JobNotReady,
    JobTimeout,
    JobView,
    ResonaAPIError,
    ResultExpired,
)

from .hub import EXIT_ERROR, EXIT_POLICY_DENIED, candidate_label, connect_hub, fail, policy_summary
from .outputs import OutputExists, OutputPaths, result_metadata, write_result

jobs_app = typer.Typer(help="Inspect and manage hub jobs.", no_args_is_help=True)

T = TypeVar("T")


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except JobNotReady:
        fail("job_not_ready: the job has not completed yet (try `resona jobs wait`)")
    except ResultExpired as e:
        if e.code == "content_unavailable":
            fail("content_unavailable: the result did not survive a hub restore")
        fail("result_expired: the result was removed by retention")
    except ResonaAPIError as e:
        if e.status == 404:
            fail("not_found: no such job in this organization")
        fail(f"{e.code or e.status}: {e.message}")
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")


def _print_view(job: JobView) -> None:
    typer.echo(json.dumps(dict(job.raw), ensure_ascii=False, indent=2))


@jobs_app.command("get")
def get_job(job_id: int = typer.Argument(..., help="Job ID.")):
    """Print a job's state as JSON."""
    client = connect_hub()
    _print_view(_call(lambda: client.get_job(job_id)))


@jobs_app.command("wait")
def wait_job(
    job_id: int = typer.Argument(..., help="Job ID."),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="Seconds to wait."),
):
    """Wait until a job is completed, failed or cancelled; print its state as JSON.

    Exits 0 when completed, 2 on policy denial, 1 otherwise (including timeout).
    """
    client = connect_hub()
    try:
        job = _call(lambda: client.wait_for_job(job_id, timeout=timeout))
    except JobTimeout:
        fail(f"timeout: job {job_id} is still running on the hub")
    typer.echo(policy_summary(job), err=True)
    _print_view(job)
    if job.status != "completed":
        code = (job.error or {}).get("code")
        raise typer.Exit(EXIT_POLICY_DENIED if code == "policy_denied" else EXIT_ERROR)


@jobs_app.command("result")
def job_result(
    job_id: int = typer.Argument(..., help="Job ID."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Write <stem>.md and <stem>.result.json here instead of printing."),
    stem: Optional[str] = typer.Option(None, "--stem", help="Output file stem (default: job-<id>)."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outputs."),
    as_json: bool = typer.Option(False, "--json", help="Print the full result JSON instead of Markdown."),
):
    """Print (or write) a completed job's result."""
    client = connect_hub()
    job = _call(lambda: client.get_job(job_id))
    result = _call(lambda: client.get_result(job_id))
    if output_dir is None:
        typer.echo(result.model_dump_json(indent=2) if as_json else (result.markdown or result.raw_text))
        return
    name = stem or f"job-{job_id}"
    paths = OutputPaths(markdown=output_dir / f"{name}.md", result=output_dir / f"{name}.result.json")
    meta = result_metadata(name, result, job_id=job_id, private=job.private,
                           execution_profile=job.execution_profile, postprocess_profile=None)
    meta.pop("source")
    try:
        write_result(paths, result, meta, force=force)
    except OutputExists as e:
        fail(str(e))
    typer.echo(json.dumps({"job_id": job_id, "markdown": str(paths.markdown), "result": str(paths.result)})
               if as_json else f"job {job_id} -> {paths.markdown}")


@jobs_app.command("cancel")
def cancel_job(job_id: int = typer.Argument(..., help="Job ID.")):
    """Cancel a job; a worker's lease is fenced and later output is rejected."""
    client = connect_hub()
    _print_view(_call(lambda: client.cancel_job(job_id)))


@jobs_app.command("delete")
def delete_job(job_id: int = typer.Argument(..., help="Job ID.")):
    """Delete a terminal job's input, result and metadata now."""
    client = connect_hub()
    _call(lambda: client.delete_job(job_id))
    typer.echo(f"deleted job {job_id}", err=True)


@jobs_app.command("list")
def list_jobs(
    limit: int = typer.Option(50, "--limit", min=1, max=100, help="Page size (max 100)."),
    cursor: Optional[str] = typer.Option(None, "--cursor", help="Cursor from a previous page."),
    as_json: bool = typer.Option(False, "--json", help="Print the page as JSON."),
):
    """List the organization's jobs, newest first."""
    client = connect_hub()
    page = _call(lambda: client.list_jobs(cursor=cursor, limit=limit))
    if as_json:
        body: dict[str, Any] = {"items": [dict(j.raw) for j in page.items], "next_cursor": page.next_cursor}
        typer.echo(json.dumps(body, ensure_ascii=False, indent=2))
        return
    for j in page.items:
        reason = f" ({j.queue_reason})" if j.queue_reason else ""
        typer.echo(f"{j.job_id}\t{j.kind}\t{j.status}{reason}\tprivate={str(j.private).lower()}\t"
                   f"{j.execution_profile}\t{candidate_label(j.candidate)}")
    if page.next_cursor:
        typer.echo(f"next: resona jobs list --cursor {page.next_cursor}", err=True)
