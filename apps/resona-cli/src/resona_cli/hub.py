"""Hub selection and error reporting shared by the hub-backed CLI commands.

The hub is chosen by ``RESONA_API_URL``/``RESONA_API_KEY`` or the ``resona-api``
entry in ``~/.resona/config.json``. Once chosen, every hub error is reported and
ends the command; nothing falls back to local or cloud processing.
"""
from __future__ import annotations

import os
from typing import NoReturn, Optional

import typer

from resona_client.client import JobView, ResonaClient
from resona_client.config import EngineConfig

EXIT_ERROR = 1
EXIT_POLICY_DENIED = 2


def hub_identity(cfg: Optional[EngineConfig] = None) -> Optional[tuple[str, str]]:
    """``(hub URL, client key)`` from env or config, without contacting the hub."""
    url = os.getenv("RESONA_API_URL")
    if url:
        return url.rstrip("/"), os.getenv("RESONA_API_KEY", "")
    cfg = cfg or EngineConfig.load()
    for entry in cfg.engines:
        if entry.type == "resona-api" and entry.api_url:
            return entry.api_url.rstrip("/"), entry.api_key or ""
    return None


def connect_hub() -> ResonaClient:
    """Client for the configured hub; exits 1 when none is configured or reachable."""
    if hub_identity() is None:
        fail("no hub configured: set RESONA_API_URL/RESONA_API_KEY or add one with "
             "`resona engines add`; use --local to process on this machine")
    try:
        return ResonaClient.from_config(auto_start=False)
    except RuntimeError as e:
        fail(f"configured hub is unreachable: {e}")


def fail(message: str, code: int = EXIT_ERROR) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def exit_policy_denied(message: str) -> NoReturn:
    typer.echo(f"policy_denied: {message}", err=True)
    raise typer.Exit(EXIT_POLICY_DENIED)


def check_privacy_flags(private: bool, allow_cloud: bool) -> bool:
    """``--private`` and ``--allow-cloud`` are mutually exclusive; the default is private."""
    if private and allow_cloud:
        fail("--private and --allow-cloud are mutually exclusive")
    return not allow_cloud


def candidate_label(candidate: Optional[dict]) -> str:
    if not candidate:
        return "unassigned"
    label = f"{candidate.get('engine')}/{candidate.get('model')}@{candidate.get('accelerator')}"
    if candidate.get("ocr_engine"):
        label += f"+{candidate['ocr_engine']}"
    return label


def policy_summary(job: JobView) -> str:
    """One stderr line: privacy, execution profile and the candidate that ran (or will)."""
    private = "true" if job.private else "false (cloud allowed)"
    return (f"job {job.job_id}: private={private} execution_profile={job.execution_profile} "
            f"candidate={candidate_label(job.candidate)} status={job.status}")
