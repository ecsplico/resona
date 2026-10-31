import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .markdown_output import write_markdown as _write_markdown

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}

_PROFILES_DIR = Path.home() / ".resona" / "profiles"


def watch_directory(
    directory: Path = typer.Argument(..., help="Directory to watch for new audio files."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Watch subdirectories too."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between directory scans."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Directory to write transcripts (default: next to each audio file)."),
    model: Optional[str] = typer.Option(None, "--model", help="Model name (approved hub override, or local engine model)."),
    language: str = typer.Option("de", "--language", help="Language hint for transcription."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout", help="Seconds to wait for local engine startup (local mode only)."),
    engine: Optional[str] = typer.Option(None, "--engine", help="Engine for local transcription (e.g. faster-whisper, whisper, voxtral). Falls back to default_engine in ~/.resona/config.json."),
    profile: Optional[str] = typer.Option(None, "--profile", help="Profile name (hub or local); a profile JSON file only in local mode."),
):
    """Watch a directory for new audio files and transcribe them.

    With a hub configured, each file becomes a private audio job; the result is
    written to `<stem>.md`. Hub errors are reported, never replaced by a local run.
    """
    from resona_client.config import EngineConfig

    from .transcribe import (
        _connect_hub,
        _hub_configured,
        _hub_profile_name,
        _resolve_local_engine_name,
    )

    cfg = EngineConfig.load()

    if not _hub_configured(cfg):
        _watch_local_fallback(
            directory, recursive, poll_interval, output_dir, model, language,
            engine_timeout, _resolve_local_engine_name(engine, cfg.default_engine),
            profile=profile, default_profile=cfg.default_profile,
        )
        return

    profile_name = _hub_profile_name(profile)
    client = _connect_hub()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    print(f"Watching {directory} for audio files (recursive={recursive})...")

    try:
        while True:
            glob_fn = directory.rglob if recursive else directory.glob
            for ext in EXTENSIONS:
                for f in glob_fn(f"*.{ext}"):
                    if f not in seen:
                        seen.add(f)
                        _transcribe_one_via_hub(client, f, output_dir, language, model, profile_name)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass


def _transcribe_one_via_hub(
    client,
    f: Path,
    output_dir: Optional[Path],
    language: str,
    model: Optional[str],
    profile: Optional[str],
) -> None:
    """Submit one file, wait for it and write `<stem>.md`. Policy denial exits 2."""
    from resona_client.client import JobTimeout, PolicyDeniedError, ResonaAPIError

    from .transcribe import _exit_hub_policy_denied, _write_hub_result

    try:
        ref = client.submit_job(
            f, kind="audio", language=language, model=model, profile=profile, private=True,
        )
        print(f"Submitted {f.name} -> job {ref.job_id}")
        job = client.wait_for_job(ref.job_id)
        if job.status != "completed":
            error = job.error or {}
            message = error.get("message") or job.status
            if error.get("code") == "policy_denied":
                _exit_hub_policy_denied(message)
            typer.echo(f"Failed to transcribe {f.name}: job {ref.job_id} {job.status}: {message}", err=True)
            return
        result = client.get_result(ref.job_id)
    except PolicyDeniedError as e:
        _exit_hub_policy_denied(e.message)
    except (httpx.HTTPError, ResonaAPIError, JobTimeout) as e:
        typer.echo(f"Failed to transcribe {f.name}: {e}", err=True)
        return
    out_path = (output_dir or f.parent) / f"{f.stem}.md"
    _write_hub_result(out_path, f.name, ref.job_id, result,
                      language=language, profile=profile, private=True)
    print(f"Transcribed {f.name} -> {out_path}")


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
        f"No hub configured — starting local engine (engine={engine}).",
        err=True,
    )

    from resona_postprocess.profile import resolve_profile, ProfileError
    from resona_postprocess.pipeline import PolicyDenied, build_pipeline, local_policy
    from .transcribe import _exit_policy_denied

    ref = profile or default_profile or "default"
    try:
        prof = resolve_profile(ref, _PROFILES_DIR)
    except ProfileError as e:
        typer.echo(
            f"Profile {ref!r} could not be loaded ({e}); falling back to 'default'.",
            err=True,
        )
        prof = resolve_profile("default", _PROFILES_DIR)

    # Watch has no --no-private flag, so its local fallback is always private.
    try:
        pipeline = build_pipeline(prof, policy=local_policy(prof, private=True))
    except PolicyDenied as e:
        _exit_policy_denied(e.stage, e.reason)

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
                                out_path = (output_dir or f.parent) / f"{f.stem}.md"
                                _write_markdown(out_path, pp_result.text, {
                                    "source": f.name,
                                    "language": result.get("language") or language,
                                    "engine": engine,
                                    "model": model,
                                    "profile": prof.name,
                                    "transcribed_at": datetime.now(timezone.utc).isoformat(),
                                })
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
    except PolicyDenied as e:
        _exit_policy_denied(e.stage, e.reason)
    except KeyboardInterrupt:
        pass
