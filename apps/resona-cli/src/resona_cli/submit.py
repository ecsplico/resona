"""resona submit — send audio to the hub's job queue and print the job IDs."""
from typing import Optional

import typer

from resona_client.client import AudioOptions, PolicyDeniedError, ResonaClient
from .transcribe import _expand_inputs, _hub_profile_name


def submit_files(
    inputs: list[str] = typer.Argument(
        ..., help="Audio files, glob patterns, or directories.", metavar="FILES..."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Approved engine override forwarded to the hub."),
    translate: bool = typer.Option(False, "--translate",
        help="Request English translation instead of transcription."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Name of a postprocess profile stored on the hub."),
    private: bool = typer.Option(True, "--private/--no-private",
        help="Require private processing (default). --no-private still needs org cloud approval."),
):
    files = _expand_inputs(inputs, recursive=False)
    if not files:
        typer.echo("No audio files found.", err=True)
        raise typer.Exit(1)

    profile_name = _hub_profile_name(profile)

    try:
        client = ResonaClient.from_config(auto_start=False)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    failed = False
    for filepath in files:
        try:
            job = client.submit_job(
                filepath,
                kind="audio",
                engine=engine,
                profile=profile_name,
                options=AudioOptions(translate=translate),
                private=private,
            )
        except PolicyDeniedError as e:
            typer.echo(f"policy_denied: {e.message}", err=True)
            raise typer.Exit(2)
        except Exception as e:
            typer.echo(f"Error submitting {filepath.name}: {e}", err=True)
            failed = True
            continue
        typer.echo(str(job.job_id))
    if failed:
        raise typer.Exit(1)
