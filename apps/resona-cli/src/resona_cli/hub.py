"""Hub selection and error reporting shared by the hub-backed CLI commands.

The hub is chosen by ``RESONA_API_URL``/``RESONA_API_KEY`` or the first hub entry
in ``~/.resona/config.json``. Once chosen, every hub error is reported and
ends the command; nothing falls back to local or cloud processing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn, Optional

import typer

from resona_client.client import JobView, ResonaClient
from resona_client.config import EngineConfig, EngineEntry

EXIT_ERROR = 1
EXIT_POLICY_DENIED = 2


def _config_entry(cfg: Optional[EngineConfig] = None) -> Optional[EngineEntry]:
    """The one config entry the CLI uses: the first with a URL."""
    cfg = cfg or EngineConfig.load()
    return next((entry for entry in cfg.engines if entry.api_url), None)


def _env_hub_url() -> Optional[str]:
    """Env hub URL in ``ResonaClient.from_config`` order: RESONA_API_URL, then WS_API_URL."""
    return os.getenv("RESONA_API_URL") or os.getenv("WS_API_URL")


def hub_identity(cfg: Optional[EngineConfig] = None) -> Optional[tuple[str, str]]:
    """``(hub URL, client key)`` from env or config, without contacting the hub."""
    url = _env_hub_url()
    if url:
        return url.rstrip("/"), os.getenv("RESONA_API_KEY") or os.getenv("WS_API_KEY", "")
    entry = _config_entry(cfg)
    if entry is None:
        return None
    return entry.api_url.rstrip("/"), entry.api_key or ""


def connect_hub() -> ResonaClient:
    """Client for exactly the hub ``hub_identity`` names; exits 1 when none is
    configured or that hub is unreachable. Never falls back to another entry."""
    identity = hub_identity()
    if identity is None:
        fail("no hub configured: set RESONA_API_URL/RESONA_API_KEY or add one with "
             "`resona engines add`; use --local to process on this machine")
    entry = None if _env_hub_url() else _config_entry()
    try:
        return ResonaClient.from_config(auto_start=False, name=entry.name if entry else None)
    except RuntimeError:
        fail(f"configured hub is unreachable: {identity[0]}")


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


INSTRUCTIONS_HELP = ("User instructions for the postprocess profile's LLM steps (sent as the user "
                     "message; the profile prompt stays the system message). Needs --profile.")
INSTRUCTIONS_FILE_HELP = "Read --instructions from this UTF-8 file."


def instructions_arg(text: Optional[str], file: Optional[Path]) -> Optional[str]:
    """``--instructions`` or ``--instructions-file`` (mutually exclusive), stripped; ``None`` if neither."""
    if text is not None and file is not None:
        fail("--instructions and --instructions-file are mutually exclusive")
    if file is not None:
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            fail(f"cannot read {file}: {e}")
    if text is None:
        return None
    if not text.strip():
        fail("instructions are empty")
    return text.strip()


def candidate_label(candidate: Optional[dict]) -> str:
    if not candidate:
        return "unassigned"
    label = f"{candidate.get('engine')}/{candidate.get('model')}@{candidate.get('accelerator')}"
    if candidate.get("ocr_engine"):
        label += f"+{candidate['ocr_engine']}"
    return label


def policy_summary(job: JobView) -> str:
    """One stderr line: privacy, execution profile and the candidate that ran (or will).
    Text (postprocess-only) jobs have no candidate."""
    private = "true" if job.private else "false (cloud allowed)"
    candidate = "none" if job.kind == "text" else candidate_label(job.candidate)
    return (f"job {job.job_id}: private={private} execution_profile={job.execution_profile} "
            f"candidate={candidate} status={job.status}")
