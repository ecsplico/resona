import glob as _glob
from pathlib import Path
from typing import Optional
import typer
import httpx

from .local_engine import LocalEngine
from .engine import InProcessEngine
from resona_client.client import ResonaClient
from resona_client.config import EngineConfig
from .engines import BUILTIN_ENGINES

EXTENSIONS = {"wav", "webm", "flac", "mp3", "m4a", "ogg", "aac"}


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
        help="Require a private engine (forwarded to gateway)."),
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
                                 engine, want_private)
        return
    except (httpx.ConnectError, httpx.TimeoutException, RuntimeError):
        typer.echo("No server reachable — running engine locally.", err=True)

    local_engine_name = engine if engine in BUILTIN_ENGINES else cfg.default_engine
    _transcribe_local_fallback(files, output_dir, model, language,
                                engine_timeout, local_engine_name)


def _transcribe_via_gateway(
    client: ResonaClient,
    files: list[Path],
    output_dir: Optional[Path],
    model: Optional[str],
    language: str,
    engine: Optional[str],
    private: bool,
) -> None:
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    for filepath in files:
        try:
            kwargs: dict = {"language": language, "private": private}
            if model:
                kwargs["model"] = model
            if engine:
                kwargs["engine"] = engine
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
