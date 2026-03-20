import time
from pathlib import Path
import typer

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
):
    """Watch a directory for new audio files and submit them for transcription."""
    from ws_client.client import WhisperClient

    client = WhisperClient()
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
