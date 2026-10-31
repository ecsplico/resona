"""``resona watch``: transcribe new audio files in a directory as private jobs."""
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer
from resona_client.client import AudioOptions

from .hub import EXIT_POLICY_DENIED, connect_hub
from .media import MediaRequest, hub_profile_name, run_media
from .outputs import output_paths
from .transcribe import EXTENSIONS


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory for <stem>.md and <stem>.result.json (default: next to each audio file)."),
    force: bool = typer.Option(False, "--force",
        help="Re-transcribe files whose outputs already exist."),
    execution_profile: Optional[str] = typer.Option(None, "--execution-profile",
        help="Execution profile (approved engine candidates)."),
    language: Optional[str] = typer.Option(None, "--language",
        help="Language hint (default: the organization's setting)."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine override; must be an approved candidate of the execution profile."),
    model: Optional[str] = typer.Option(None, "--model",
        help="Model override; must be an approved candidate of the execution profile."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Postprocess profile name (a JSON file or inline JSON only with --local)."),
    local: bool = typer.Option(False, "--local",
        help="Process on this machine with cached hub settings instead of the hub."),
):
    """Watch a directory and transcribe each new audio file as a private job.

    Each file is processed like `resona transcribe --private`. Hub errors are
    reported per file and never replaced by a local run; a policy denial stops
    watching (exit 2).
    """
    if not local:
        hub_profile_name(profile)
        connect_hub()  # fail fast: no hub configured or reachable
    template = MediaRequest(
        kind="audio", files=[], output_dir=output_dir, force=force, as_json=False,
        no_wait=False, wait_timeout=None, private=True, execution_profile=execution_profile,
        language=language, engine=engine, model=model, profile=profile,
        options=AudioOptions(), local=local,
    )
    seen: set[Path] = set()
    typer.echo(f"Watching {directory} for audio files (recursive={recursive}, "
               f"{'local' if local else 'hub'})...", err=True)
    try:
        while True:
            glob_fn = directory.rglob if recursive else directory.glob
            for ext in sorted(EXTENSIONS):
                for f in sorted(glob_fn(f"*.{ext}")):
                    if f in seen:
                        continue
                    seen.add(f)
                    if not force and output_paths(f, output_dir).existing():
                        continue
                    try:
                        run_media(replace(template, files=[f]))
                    except typer.Exit as e:
                        if e.exit_code == EXIT_POLICY_DENIED:
                            raise
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
