import glob as _glob
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .engine import InProcessEngine

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


def _expand_inputs(inputs: list[str], recursive: bool) -> list[Path]:
    """Expand a list of file paths, glob patterns, and/or directories into audio files.

    - A literal file path is included as-is.
    - A glob pattern (`*`, `?`, `[...]`) is expanded against the cwd; matching files
      with an audio extension are included.
    - A directory is scanned for audio files (recursively if `recursive`).
    """
    out: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp in seen:
            return
        seen.add(rp)
        out.append(p)

    for raw in inputs:
        if any(ch in raw for ch in "*?["):
            matches = [Path(m) for m in _glob.glob(raw, recursive=recursive)]
            for m in matches:
                if m.is_file() and m.suffix.lstrip(".").lower() in EXTENSIONS:
                    _add(m)
            continue

        p = Path(raw)
        if p.is_dir():
            glob_fn = p.rglob if recursive else p.glob
            for ext in EXTENSIONS:
                for f in glob_fn(f"*.{ext}"):
                    _add(f)
        elif p.is_file():
            _add(p)
        else:
            typer.echo(f"Not found: {raw}", err=True)

    return out


def transcribe_files(
    inputs: list[str] = typer.Argument(
        ...,
        help="Audio files, glob patterns (e.g. 'folder/*.mp3'), or directories.",
        metavar="INPUTS...",
    ),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
    backend: Optional[str] = typer.Option(None, "--backend", help="Backend for local engine (e.g. faster-whisper, whisper, voxtral). Falls back to default_backend in ~/.resona/config.json."),
):
    """Transcribe audio files. Accepts files, glob patterns, or directories."""
    from resona_client.client import ResonaClient
    from resona_client.config import BackendConfig

    resolved_backend = backend or BackendConfig.load().default_backend
    files = _expand_inputs(inputs, recursive=recursive)

    try:
        client = ResonaClient.from_config()
    except RuntimeError:
        _transcribe_local_fallback(files, output_dir, model, language, engine_timeout, resolved_backend)
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
                transcript = job.get("md", "") or job.get("transcript", "")
                out_path = output_dir / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"  -> Saved to {out_path}")
        except TimeoutError:
            print(f"Timeout waiting for job {job_id} ({filepath.name})")
        except Exception as e:
            print(f"Error for job {job_id} ({filepath.name}): {e}")


def _transcribe_local_fallback(
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

    engine, cleanup = _resolve_local_engine(model, engine_timeout, backend)
    pipeline = build_pipeline_from_config()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
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
    finally:
        cleanup()


def _resolve_local_engine(model, engine_timeout, backend):
    """Return (engine, cleanup_fn). Prefer in-process; fall back to subprocess.

    The in-process path is preferred when ``resona-asr-core`` and a backend extra
    are installed in the same environment as the CLI. Otherwise the original
    subprocess-based LocalEngine spawns ``resona-engine-<backend>`` and HTTPs
    against it.
    """
    try:
        engine = InProcessEngine(backend=backend)
        typer.echo(
            f"No server reachable — running backend '{backend}' in-process.",
            err=True,
        )
        return engine, (lambda: None)
    except ImportError:
        typer.echo(
            f"No server reachable — starting local engine subprocess (backend={backend}).",
            err=True,
        )
        ctx = LocalEngine(model=model, timeout=engine_timeout, backend=backend)
        engine = ctx.__enter__()
        return engine, (lambda: ctx.__exit__(None, None, None))
