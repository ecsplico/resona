import json
import time
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .profiles import resolve_profile_arg as _resolve_profile_arg
from resona_postprocess.profile import resolve_profile, ProfileError
from resona_postprocess.pipeline import build_pipeline

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}

_PROFILES_DIR = Path.home() / ".resona" / "profiles"


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts."),
    model: Optional[str] = typer.Option(None, "--model", help="Whisper model name (local fallback only)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription (local fallback only)."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local fallback only)."),
    engine: Optional[str] = typer.Option(None, "--engine", help="Engine for local transcription (e.g. faster-whisper, whisper, voxtral). Falls back to default_engine in ~/.resona/config.json."),
    profile: Optional[str] = typer.Option(None, "--profile", help="Profile name or path to a profile JSON file."),
):
    """Watch a directory for new audio files and submit them for transcription."""
    from resona_client.client import ResonaClient
    from resona_client.config import EngineConfig

    cfg = EngineConfig.load()
    resolved_engine = engine or cfg.default_engine

    try:
        client = ResonaClient.from_config()
    except RuntimeError:
        _watch_local_fallback(
            directory, recursive, poll_interval, output_dir, model, language,
            engine_timeout, resolved_engine, profile=profile,
            default_profile=cfg.default_profile,
        )
        return

    if model is not None:
        typer.echo(
            "--model is only used in local fallback mode and will be ignored.",
            err=True,
        )

    resolved_profile = _resolve_profile_arg(profile)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (recursive={recursive})...")

    while True:
        glob_fn = directory.rglob if recursive else directory.glob
        for ext in EXTENSIONS:
            for f in glob_fn(f"*.{ext}"):
                if f not in seen:
                    seen.add(f)
                    try:
                        submit_kwargs = {}
                        if resolved_profile is not None:
                            submit_kwargs["profile"] = resolved_profile
                        result = client.submit_job(f, **submit_kwargs)
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
    engine: str = "faster-whisper",
    *,
    profile: Optional[str] = None,
    default_profile: Optional[str] = None,
) -> None:
    typer.echo(
        f"No server reachable — starting local engine (engine={engine}).",
        err=True,
    )

    ref = profile or default_profile or "default"
    try:
        prof = resolve_profile(ref, _PROFILES_DIR)
    except ProfileError as e:
        typer.echo(
            f"Profile {ref!r} could not be loaded ({e}); falling back to 'default'.",
            err=True,
        )
        prof = resolve_profile("default", _PROFILES_DIR)

    pipeline = build_pipeline(prof)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (local fallback, recursive={recursive})...")

    try:
        with LocalEngine(model=model, timeout=engine_timeout, engine=engine) as engine_obj:
            while True:
                glob_fn = directory.rglob if recursive else directory.glob
                for ext in EXTENSIONS:
                    for f in glob_fn(f"*.{ext}"):
                        if f not in seen:
                            seen.add(f)
                            try:
                                result = engine_obj.transcribe(f, language=language)
                                raw_text = result.get("text", "")
                                pp_result = pipeline.run(raw_text)
                                out_path = (output_dir or f.parent) / f"{f.stem}.txt"
                                out_path.write_text(pp_result.text, encoding="utf-8")
                                if pp_result.data:
                                    sidecar = out_path.with_suffix(".json")
                                    sidecar.write_text(
                                        json.dumps(pp_result.data, ensure_ascii=False, indent=2),
                                        encoding="utf-8",
                                    )
                                print(f"Transcribed {f.name} -> {out_path}")
                            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                                typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
