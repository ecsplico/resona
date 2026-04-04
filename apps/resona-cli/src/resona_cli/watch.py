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
    backend: Optional[str] = typer.Option(None, "--backend", help="Backend for local engine (e.g. faster-whisper, whisper, voxtral). Falls back to default_backend in ~/.resona/config.json."),
):
    """Watch a directory for new audio files and submit them for transcription."""
    from resona_client.client import ResonaClient
    from resona_client.config import BackendConfig

    resolved_backend = backend or BackendConfig.load().default_backend

    try:
        client = ResonaClient.from_config()
    except RuntimeError:
        _watch_local_fallback(
            directory, recursive, poll_interval, output_dir, model, language, engine_timeout, resolved_backend
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
    backend: str = "faster-whisper",
) -> None:
    from resona_postprocess.sources import build_pipeline_from_config

    typer.echo(
        f"No server reachable — starting local engine (backend={backend}).",
        err=True,
    )

    pipeline = build_pipeline_from_config()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (local fallback, recursive={recursive})...")

    try:
        with LocalEngine(model=model, timeout=engine_timeout, backend=backend) as engine:
            while True:
                glob_fn = directory.rglob if recursive else directory.glob
                for ext in EXTENSIONS:
                    for f in glob_fn(f"*.{ext}"):
                        if f not in seen:
                            seen.add(f)
                            try:
                                result = engine.transcribe(f, language=language)
                                raw_text = result.get("text", "")
                                transcript = pipeline.run(raw_text)
                                out_path = (output_dir or f.parent) / f"{f.stem}.txt"
                                out_path.write_text(transcript, encoding="utf-8")
                                print(f"Transcribed {f.name} -> {out_path}")
                            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                                typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
