import time
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
):
    """Watch a directory for new audio files and submit them for transcription."""
    from resona_client.client import ResonaClient

    try:
        client = ResonaClient.from_config()
    except RuntimeError:
        _watch_local_fallback(
            directory, recursive, poll_interval, output_dir, model, language, engine_timeout
        )
        return

    if model is not None:
        typer.echo(
            "--model is only used in local fallback mode and will be ignored.",
            err=True,
        )

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (recursive={recursive})...")

    while True:
        glob_fn = directory.rglob if recursive else directory.glob
        for ext in EXTENSIONS:
            for f in glob_fn(f"*.{ext}"):
                if f not in seen:
                    seen.add(f)
                    try:
                        result = client.submit_job(f)
                        print(f"Submitted {f.name} -> job {result['id']}")
                    except Exception as e:
                        print(f"Failed to submit {f.name}: {e}")
        time.sleep(poll_interval)


def _watch_local_fallback(
    directory: Path,
    recursive: bool,
    poll_interval: float,
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
) -> None:
    typer.echo(
        "No server reachable — starting local engine (replacements and prompts not available).",
        err=True,
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (local fallback, recursive={recursive})...")

    try:
        with LocalEngine(model=model, timeout=engine_timeout) as engine:
            while True:
                glob_fn = directory.rglob if recursive else directory.glob
                for ext in EXTENSIONS:
                    for f in glob_fn(f"*.{ext}"):
                        if f not in seen:
                            seen.add(f)
                            try:
                                result = engine.transcribe(f, language=language)
                                transcript = result.get("text", "")
                                out_path = (output_dir or f.parent) / f"{f.stem}.txt"
                                out_path.write_text(transcript, encoding="utf-8")
                                print(f"Transcribed {f.name} -> {out_path}")
                            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                                typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
