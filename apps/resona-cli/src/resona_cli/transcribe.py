import glob as _glob
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .engine import CloudEngine, InProcessEngine
from resona_client.client import ResonaClient
from resona_client.config import EngineConfig, resolve_engine
from .engines import BUILTIN_ENGINES

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
        ..., help="Audio files, glob patterns, or directories.", metavar="INPUTS..."),
    recursive: bool = typer.Option(False, "--recursive", "-r",
        help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model",
        help="Model name override (local fallback and cloud engines)."),
    language: str = typer.Option("de", "--language",
        help="Language hint for transcription."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout",
        help="Seconds to wait for local engine startup (local fallback only)."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine name: a built-in local engine, or a config.json server/cloud entry."),
    private: Optional[bool] = typer.Option(None, "--private/--no-private",
        help="Require a private engine. Defaults to default_private in config.json."),
):
    """Transcribe audio files. Accepts files, glob patterns, or directories."""
    cfg = EngineConfig.load()
    want_private = cfg.default_private if private is None else private
    files = _expand_inputs(inputs, recursive=recursive)
    if not files:
        print("No audio files found.")
        return

    target = _resolve_target(engine, cfg, want_private)
    if target is None:
        return  # _resolve_target already printed the error

    kind, value = target
    if kind == "cloud":
        _transcribe_cloud(files, output_dir, value, model, language)
    elif kind == "resona-api":
        _transcribe_via_client(files, output_dir, value, model)
    else:  # kind == "local"
        _transcribe_local_fallback(files, output_dir, model, language,
                                   engine_timeout, value)


def _resolve_target(engine, cfg, want_private):
    """Resolve --engine into ('cloud'|'resona-api'|'local', payload).

    Returns None (after printing an error) when resolution fails.
    """
    if engine is not None:
        entry = cfg.get(engine)
        if entry is not None:
            if want_private and not entry.is_private():
                typer.echo(
                    f"Engine '{engine}' is not private — refused under --private",
                    err=True,
                )
                raise typer.Exit(1)
            return (entry.type, entry)
        if engine in BUILTIN_ENGINES:
            return ("local", engine)
        typer.echo(f"Unknown engine '{engine}'.", err=True)
        raise typer.Exit(1)

    # No --engine: try config entries by priority, then a local engine.
    entry = resolve_engine(private_only=want_private)
    if entry is not None:
        return (entry.type, entry)
    return ("local", cfg.default_engine)


def _transcribe_cloud(files, output_dir, entry, model, language):
    from resona_postprocess.sources import build_pipeline_from_config
    from resona_cloud_stt.errors import CloudSTTError

    cloud = CloudEngine(entry)
    pipeline = build_pipeline_from_config()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Transcribing via cloud engine '{entry.name}' ({entry.provider}).",
               err=True)
    for filepath in files:
        try:
            kwargs = {"language": language}
            if model is not None:
                kwargs["model"] = model
            result = cloud.transcribe(filepath, **kwargs)
            transcript = pipeline.run(result.get("text", ""))
            out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
            out_path.write_text(transcript, encoding="utf-8")
            print(f"Transcribed {filepath.name} -> {out_path}")
        except CloudSTTError as e:
            typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)


def _transcribe_via_client(files, output_dir, entry, model):
    if model is not None:
        typer.echo("--model is ignored when submitting to a resona-api server.",
                   err=True)
    client = ResonaClient(base_url=entry.api_url, api_key=entry.api_key)
    _submit_and_wait(client, files, output_dir)


def _submit_and_wait(client, files, output_dir):
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
    engine: str = "faster-whisper",
) -> None:
    from resona_postprocess.sources import build_pipeline_from_config

    if not files:
        print("No audio files found.")
        return

    local_engine, cleanup = _resolve_local_engine(model, engine_timeout, engine)
    pipeline = build_pipeline_from_config()

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in files:
            try:
                result = local_engine.transcribe(filepath, language=language)
                raw_text = result.get("text", "")
                transcript = pipeline.run(raw_text)
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(transcript, encoding="utf-8")
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
    finally:
        cleanup()


def _resolve_local_engine(model, engine_timeout, engine):
    """Return (engine_obj, cleanup_fn). Prefer in-process; fall back to subprocess.

    The in-process path is preferred when ``resona-asr-core`` and an engine extra
    are installed in the same environment as the CLI. Otherwise the original
    subprocess-based LocalEngine spawns ``resona-engine-<engine>`` and HTTPs
    against it.
    """
    try:
        engine_obj = InProcessEngine(engine=engine)
        typer.echo(
            f"No server reachable — running engine '{engine}' in-process.",
            err=True,
        )
        return engine_obj, (lambda: None)
    except ImportError:
        typer.echo(
            f"No server reachable — starting local engine subprocess (engine={engine}).",
            err=True,
        )
        ctx = LocalEngine(model=model, timeout=engine_timeout, engine=engine)
        engine_obj = ctx.__enter__()
        return engine_obj, (lambda: ctx.__exit__(None, None, None))
