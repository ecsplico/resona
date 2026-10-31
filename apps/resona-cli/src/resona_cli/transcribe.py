import glob as _glob
import itertools
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Optional
import typer
import httpx

from .markdown_output import write_markdown as _write_markdown


@contextmanager
def _spinner(label: str):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    stop = threading.Event()

    def _spin():
        for frame in itertools.cycle(frames):
            if stop.is_set():
                break
            sys.stderr.write(f"\r{frame} {label}")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(label) + 2) + "\r")
        sys.stderr.flush()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()

from .local_engine import LocalEngine, probe_local_engine, DEFAULT_LOCAL_ENGINE_PORT
from .engine import InProcessEngine
from resona_client.client import (
    JobTimeout,
    PolicyDeniedError,
    ResonaAPIError,
    ResonaClient,
)
from resona_client.config import EngineConfig
from .engines import BUILTIN_ENGINES
EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}

_PROFILES_DIR = Path.home() / ".resona" / "profiles"


def _resolve_local_engine_name(engine: Optional[str], default_engine: str) -> str:
    """Pick the engine to run locally when no server is reachable.

    Priority: explicit built-in ``--engine`` > a concrete ``default_engine`` from
    config > environment-aware default (``recommended_engine`` — GPU-native on
    Apple Silicon, faster-whisper elsewhere), limited to engines installed here.
    """
    if engine and engine in BUILTIN_ENGINES:
        return engine
    if default_engine and default_engine != "auto":
        return default_engine
    try:
        from resona_asr_core.registry import recommended_engine
        return recommended_engine()
    except Exception:
        return "faster-whisper"


def _expand_inputs(inputs: list[str], recursive: bool) -> list[Path]:
    """Expand file paths, glob patterns, and/or directories into audio files."""
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
        help="Model name forwarded to the gateway engine."),
    language: str = typer.Option("de", "--language",
        help="Language hint for transcription."),
    engine_timeout: float = typer.Option(120.0, "--engine-timeout",
        help="Seconds to wait for local engine startup (local fallback only)."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine name forwarded to the gateway, or a built-in local engine for fallback."),
    private: Optional[bool] = typer.Option(None, "--private/--no-private",
        help="Require private processing (default). --no-private still needs org cloud approval."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Profile name (hub or local); a profile JSON file only in local mode."),
):
    """Transcribe audio files through the configured hub, or locally when no hub is configured.

    Once a hub is configured (RESONA_API_URL or a resona-api entry in
    ~/.resona/config.json), every hub error is reported; nothing falls back to a
    local or cloud engine.
    """
    files = _expand_inputs(inputs, recursive=recursive)
    if not files:
        print("No audio files found.")
        return

    cfg = EngineConfig.load()
    want_private = True if private is None else private

    if _hub_configured(cfg):
        profile_name = _hub_profile_name(profile)
        client = _connect_hub()
        _transcribe_via_hub(client, files, output_dir, model, language,
                            engine, want_private, profile_name)
        return

    local_engine_name = _resolve_local_engine_name(engine, cfg.default_engine)
    # Local runs are private unless --no-private is given; cloud stages stay denied otherwise.
    _transcribe_local_fallback(files, output_dir, model, language,
                                engine_timeout, local_engine_name,
                                profile=profile,
                                default_profile=cfg.default_profile,
                                private=want_private)


def _hub_configured(cfg: EngineConfig) -> bool:
    """True when the user explicitly configured a Resona hub."""
    if os.getenv("RESONA_API_URL"):
        return True
    return any(e.type == "resona-api" and e.api_url for e in cfg.engines)


def _hub_profile_name(profile: Optional[str]) -> Optional[str]:
    """Return a hub profile name; profile files and inline JSON are local-mode only."""
    if profile is None:
        return None
    path = Path(profile)
    if profile.lstrip().startswith("{") or (path.suffix == ".json" and path.exists()):
        typer.echo(
            "Error: with a hub configured, --profile takes the name of a profile stored "
            "on the hub; profile files and inline JSON are only supported in local mode.",
            err=True,
        )
        raise typer.Exit(1)
    return profile


def _connect_hub() -> ResonaClient:
    try:
        return ResonaClient.from_config(auto_start=False)
    except RuntimeError as e:
        typer.echo(f"Error: configured hub is unreachable: {e}", err=True)
        raise typer.Exit(1)


def _exit_hub_policy_denied(message: str) -> NoReturn:
    typer.echo(f"policy_denied: {message}", err=True)
    raise typer.Exit(code=2)


def _transcribe_via_hub(
    client: ResonaClient,
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine: Optional[str],
    private: bool,
    profile: Optional[str] = None,
) -> None:
    """Submit every file as an audio job, wait for each and write `<stem>.md`.

    Policy denial exits 2; any other hub error or unsuccessful job exits 1.
    """
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    try:
        submitted = [
            (filepath, client.submit_job(
                filepath,
                kind="audio",
                language=language,
                engine=engine,
                model=model,
                profile=profile,
                private=private,
            ))
            for filepath in files
        ]
        for filepath, ref in submitted:
            with _spinner(f"Transcribing {filepath.name} (job {ref.job_id})…"):
                job = client.wait_for_job(ref.job_id)
            if job.status != "completed":
                error = job.error or {}
                message = error.get("message") or job.status
                if error.get("code") == "policy_denied":
                    _exit_hub_policy_denied(message)
                typer.echo(
                    f"Failed to transcribe {filepath.name}: job {ref.job_id} {job.status}: {message}",
                    err=True,
                )
                failures += 1
                continue
            result = client.get_result(ref.job_id)
            out_path = (output_dir or filepath.parent) / f"{filepath.stem}.md"
            _write_hub_result(out_path, filepath.name, ref.job_id, result,
                              language=language, profile=profile, private=private)
            print(f"Transcribed {filepath.name} -> {out_path}")
    except PolicyDeniedError as e:
        _exit_hub_policy_denied(e.message)
    except (httpx.HTTPError, ResonaAPIError, JobTimeout, OSError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    if failures:
        raise typer.Exit(1)


def _write_hub_result(
    out_path: Path,
    source: str,
    job_id: int,
    result,
    *,
    language: str,
    profile: Optional[str],
    private: bool,
) -> None:
    """Write a hub job result's Markdown with frontmatter from its ASR provenance."""
    asr = next((s for s in result.provenance.stages if s.stage == "asr"), None)
    _write_markdown(out_path, result.markdown or result.raw_text, {
        "source": source,
        "job_id": job_id,
        "language": result.language or language,
        "engine": asr.engine if asr else None,
        "model": asr.model if asr else None,
        "profile": profile,
        "private": private,
        "worker": result.provenance.worker_id,
        "transcribed_at": datetime.now(timezone.utc).isoformat(),
    })


def _exit_policy_denied(stage: str, reason: str) -> NoReturn:
    typer.echo(f"policy_denied: {stage}: {reason}", err=True)
    raise typer.Exit(code=2)


def _transcribe_local_fallback(
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine_timeout: float,
    engine: str = "faster-whisper",
    *,
    profile: Optional[str] = None,
    default_profile: Optional[str] = None,
    private: bool = True,
) -> None:
    from resona_postprocess.profile import resolve_profile, ProfileError
    from resona_postprocess.pipeline import PolicyDenied, build_pipeline, local_policy

    ref = profile or default_profile or "default"
    try:
        prof = resolve_profile(ref, _PROFILES_DIR)
    except ProfileError as e:
        typer.echo(
            f"Profile {ref!r} could not be loaded ({e}); falling back to 'default'.",
            err=True,
        )
        prof = resolve_profile("default", _PROFILES_DIR)

    try:
        pipeline = build_pipeline(prof, policy=local_policy(prof, private=private))
    except PolicyDenied as e:
        _exit_policy_denied(e.stage, e.reason)

    local_engine, cleanup = _resolve_local_engine(model, engine_timeout, engine)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in files:
            try:
                with _spinner(f"Transcribing {filepath.name}…"):
                    result = local_engine.transcribe(filepath, language=language)
                raw_text = result.get("text", "")
                pp_result = pipeline.run(raw_text)
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.md"
                _write_markdown(out_path, pp_result.text, {
                    "source": filepath.name,
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
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
    except PolicyDenied as e:
        _exit_policy_denied(e.stage, e.reason)
    finally:
        cleanup()


def _resolve_local_engine(model, engine_timeout, engine):
    if probe_local_engine():
        typer.echo(
            f"Found a local engine already running on port {DEFAULT_LOCAL_ENGINE_PORT} — using it.",
            err=True,
        )
        ctx = LocalEngine(model=model, timeout=engine_timeout, engine=engine)
        engine_obj = ctx.__enter__()
        return engine_obj, (lambda: ctx.__exit__(None, None, None))
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
