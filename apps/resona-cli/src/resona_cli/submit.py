"""resona submit — send audio to the async job queue and print result URLs."""
from pathlib import Path
from typing import Optional

import typer

from resona_client.client import ResonaClient
from .transcribe import _expand_inputs


def submit_files(
    inputs: list[str] = typer.Argument(
        ..., help="Audio files, glob patterns, or directories.", metavar="FILES..."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine name to forward to the gateway."),
    translate: bool = typer.Option(False, "--translate",
        help="Request English translation instead of transcription."),
):
    files = _expand_inputs(inputs, recursive=False)
    if not files:
        typer.echo("No audio files found.", err=True)
        raise typer.Exit(1)

    try:
        client = ResonaClient.from_config(auto_start=False)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    for filepath in files:
        try:
            job = client.submit_job(filepath, translate=translate, engine=engine)
            job_id = job["id"]
            typer.echo(f"{client.base_url}/job/{job_id}")
        except Exception as e:
            typer.echo(f"Error submitting {filepath.name}: {e}", err=True)
