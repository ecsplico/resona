from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def batch_transcribe(
    directory: Path = typer.Argument(..., help="Directory of audio files to transcribe."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Include subdirectories."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Backend for local engine (e.g. faster-whisper, whisper, voxtral). Falls back to default_backend in ~/.resona/config.json."),
):
    """Transcribe all audio files in a directory (submit + wait for results)."""
    from resona_client.client import ResonaClient
    from resona_client.config import BackendConfig

    resolved_backend = backend or BackendConfig.load().default_backend

    glob_fn = directory.rglob if recursive else directory.glob
    files = [f for ext in EXTENSIONS for f in glob_fn(f"*.{ext}")]

    try:
        client = ResonaClient.from_config()
    except RuntimeError:
        _batch_local_fallback(files, output_dir, model, language, engine_timeout, resolved_backend)
        return

    if model is not None:
        typer.echo(
            "--model is only used in local fallback mode and will be ignored.",
            err=True,
        )

    if not files:
        print("No audio files found.")
        return

    jobs: list[tuple[Path, int]] = []
    for f in files:
        try:
            result = client.submit_job(f)
            job_id = result["id"]
            jobs.append((f, job_id))
            print(f"Submitted {f.name} -> job {job_id}")
        except Exception as e:
            print(f"Failed to submit {f.name}: {e}")

    if not jobs:
        print("No audio files found.")
        return

    print(f"\nWaiting for {len(jobs)} job(s) to complete...")

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for filepath, job_id in jobs:
        try:
            job = client.wait_for_job(job_id)
            job_status = job.get("status", "unknown")
            print(f"Completed {filepath.name}: {job_status}")
            if output_dir and job_status == "completed":
                transcript = job.get("transcript", "") or job.get("md", "")
                out_path = output_dir / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"  -> Saved to {out_path}")
        except TimeoutError:
            print(f"Timeout waiting for job {job_id} ({filepath.name})")
        except Exception as e:
            print(f"Error for job {job_id} ({filepath.name}): {e}")


def _batch_local_fallback(
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
    backend: str = "faster-whisper",
) -> None:
    from resona_postprocess.sources import build_pipeline_from_config

    if not files:
        print("No audio files found.")
        return

    typer.echo(
        f"No server reachable — starting local engine (backend={backend}).",
        err=True,
    )

    pipeline = build_pipeline_from_config()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    with LocalEngine(model=model, timeout=engine_timeout, backend=backend) as engine:
        for filepath in files:
            try:
                result = engine.transcribe(filepath, language=language)
                raw_text = result.get("text", "")
                transcript = pipeline.run(raw_text)
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
