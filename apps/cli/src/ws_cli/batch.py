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
):
    """Transcribe all audio files in a directory (submit + wait for results)."""
    from ws_client.client import WhisperClient

    glob_fn = directory.rglob if recursive else directory.glob
    files = [f for ext in EXTENSIONS for f in glob_fn(f"*.{ext}")]

    try:
        client = WhisperClient.from_config()
    except RuntimeError:
        _batch_local_fallback(files, output_dir, model, language, engine_timeout)
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
) -> None:
    if not files:
        print("No audio files found.")
        return

    typer.echo(
        "No server reachable — starting local engine "
        "(replacements and prompts not available in local fallback mode).",
        err=True,
    )

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    with LocalEngine(model=model, timeout=engine_timeout) as engine:
        for filepath in files:
            try:
                result = engine.transcribe(filepath, language=language)
                transcript = result.get("text", "")
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
