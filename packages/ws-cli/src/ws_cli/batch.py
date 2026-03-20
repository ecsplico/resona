from pathlib import Path
from typing import Optional
import typer

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def batch_transcribe(
    directory: Path = typer.Argument(..., help="Directory of audio files to transcribe."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Include subdirectories."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
):
    """Transcribe all audio files in a directory (submit + wait for results)."""
    from ws_client.client import WhisperClient

    client = WhisperClient()
    jobs: list[tuple[Path, int]] = []

    glob_fn = directory.rglob if recursive else directory.glob
    for ext in EXTENSIONS:
        for f in glob_fn(f"*.{ext}"):
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
