import glob as _glob
import json
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .engine import InProcessEngine
from resona_client.client import ResonaClient
from resona_client.config import EngineConfig
from .engines import BUILTIN_ENGINES
from resona_postprocess.profile import resolve_profile, ProfileError
from resona_postprocess.pipeline import build_pipeline

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}

_PROFILES_DIR = Path.home() / ".resona" / "profiles"


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


def _resolve_profile_arg(profile_arg: Optional[str]) -> Optional[str]:
    """If profile_arg is a path to an existing .json file, read and return its text.
    Otherwise return the name string as-is (or None)."""
    if profile_arg is None:
        return None
    p = Path(profile_arg)
    if p.suffix == ".json" and p.exists():
        return p.read_text(encoding="utf-8")
    return profile_arg


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
        help="Require a private engine (forwarded to gateway)."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Profile name or path to a profile JSON file."),
):
    """Transcribe audio files. Uses the gateway by default; falls back to a local engine."""
    files = _expand_inputs(inputs, recursive=recursive)
    if not files:
        print("No audio files found.")
        return

    cfg = EngineConfig.load()
    want_private = cfg.default_private if private is None else private

    try:
        client = ResonaClient.from_config(auto_start=False)
        _transcribe_via_gateway(client, files, output_dir, model, language,
                                 engine, want_private, profile)
        return
    except (httpx.ConnectError, httpx.TimeoutException, RuntimeError):
        pass

    local_engine_name = engine if engine in BUILTIN_ENGINES else cfg.default_engine
    # Local engines are inherently private; --private is honoured by the fallback
    # path naturally (no audio leaves the machine).
    _transcribe_local_fallback(files, output_dir, model, language,
                                engine_timeout, local_engine_name,
                                profile=profile,
                                default_profile=cfg.default_profile)


def _transcribe_via_gateway(
    client: ResonaClient,
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine: Optional[str],
    private: bool,
    profile: Optional[str] = None,
) -> None:
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    resolved_profile = _resolve_profile_arg(profile)
    for filepath in files:
        try:
            kwargs: dict = {"language": language, "private": private}
            if model:
                kwargs["model"] = model
            if engine:
                kwargs["engine"] = engine
            if resolved_profile is not None:
                kwargs["profile"] = resolved_profile
            result = client.create_transcription(filepath, **kwargs)
            transcript = result.get("text", "")
            out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
            out_path.write_text(transcript, encoding="utf-8")
            print(f"Transcribed {filepath.name} -> {out_path}")
        except httpx.HTTPStatusError as e:
            typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)


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
) -> None:
    ref = profile or default_profile or "default"
    try:
        prof = resolve_profile(ref, _PROFILES_DIR)
    except ProfileError as e:
        typer.echo(
            f"Profile {ref!r} could not be loaded ({e}); falling back to 'default'.",
            err=True,
        )
        prof = resolve_profile("default", _PROFILES_DIR)

    local_engine, cleanup = _resolve_local_engine(model, engine_timeout, engine)
    pipeline = build_pipeline(prof)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for filepath in files:
            try:
                result = local_engine.transcribe(filepath, language=language)
                raw_text = result.get("text", "")
                pp_result = pipeline.run(raw_text)
                out_path = (output_dir or filepath.parent) / f"{filepath.stem}.txt"
                out_path.write_text(pp_result.text, encoding="utf-8")
                if pp_result.data:
                    sidecar = out_path.with_suffix(".json")
                    sidecar.write_text(
                        json.dumps(pp_result.data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                print(f"Transcribed {filepath.name} -> {out_path}")
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                typer.echo(f"Failed to transcribe {filepath.name}: {e}", err=True)
    finally:
        cleanup()


def _resolve_local_engine(model, engine_timeout, engine):
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
