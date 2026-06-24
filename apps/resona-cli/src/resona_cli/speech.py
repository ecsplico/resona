"""resona speech — synthesise speech from text via the gateway, or locally.

Mirrors ``transcribe``: uses the gateway TTS engine when reachable, and falls
back to a torch-free local engine (Piper by default) when no server is running,
so ``resona speech`` works offline out of the box.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import typer

from resona_client.client import ResonaClient


def speak(
    text: str = typer.Argument(..., help="Text to synthesise."),
    output: Optional[str] = typer.Option(None, "--output",
        help="Output file path. Use '-' for stdout. Defaults to speech.mp3 in cwd."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine name forwarded to the gateway, or a local TTS engine for fallback."),
    voice: Optional[str] = typer.Option(None, "--voice",
        help="Voice name. Omit to use the engine's default voice."),
    language: str = typer.Option("de", "--language", "-l",
        help="Language hint (used by local engines for voice selection)."),
    model: str = typer.Option("tts-1", "--model", help="TTS model name."),
    fmt: str = typer.Option("mp3", "--format",
        help="Output format: mp3, opus, aac, flac."),
    speed: float = typer.Option(1.0, "--speed", help="Speech speed (0.25–4.0)."),
    private: bool = typer.Option(False, "--private",
        help="Require a private engine."),
    play: bool = typer.Option(False, "--play",
        help="Pipe audio to a local player (aplay, afplay, mpv) instead of saving."),
):
    """Synthesise speech from TEXT. Uses the gateway; falls back to a local engine."""
    if play and output == "-":
        typer.echo("Error: --play and --output - are mutually exclusive.", err=True)
        raise typer.Exit(1)

    try:
        client = ResonaClient.from_config(auto_start=False)
    except RuntimeError:
        # No engine configured/reachable at all — synthesise locally.
        _speak_local_fallback(text, output, engine, voice, language, speed, play, fmt)
        return

    try:
        kwargs: dict = {
            "model": model,
            "response_format": fmt,
            "speed": speed,
            "private": private,
        }
        if voice is not None:
            kwargs["voice"] = voice
        if engine:
            kwargs["engine"] = engine
        audio = client.create_speech(text, **kwargs)
    except httpx.HTTPStatusError as e:
        # A reachable gateway returning an error is a real failure — surface it.
        typer.echo(f"Error {e.response.status_code}: {e.response.text}", err=True)
        raise typer.Exit(1)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        typer.echo(f"Gateway unreachable ({e}); synthesising locally.", err=True)
        _speak_local_fallback(text, output, engine, voice, language, speed, play, fmt)
        return
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    _emit(audio, output, play, fmt, default_name="speech.mp3")


def _speak_local_fallback(
    text: str,
    output: Optional[str],
    engine: Optional[str],
    voice: Optional[str],
    language: str,
    speed: float,
    play: bool,
    fmt: str,
) -> None:
    """Synthesise with an in-process local TTS engine (torch-free Piper default)."""
    try:
        from resona_tts_local.registry import (
            ENGINES,
            get_engine,
            recommended_offline_engine,
        )
        from resona_tts_local.errors import (
            EngineUnavailableError,
            UnknownEngineError,
        )
    except ImportError:
        typer.echo(
            "Error: no gateway reachable and resona-tts-local is not installed.",
            err=True,
        )
        raise typer.Exit(1)

    name = engine if engine in ENGINES else recommended_offline_engine()
    typer.echo(f"No server reachable — synthesising locally with '{name}'.", err=True)

    if fmt != "wav":
        typer.echo(
            f"Note: --format {fmt} is ignored offline; writing WAV.", err=True
        )

    try:
        result = get_engine(name).synthesize(
            text, voice=voice, language=language, speed=speed
        )
    except (EngineUnavailableError, UnknownEngineError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    _emit(result["audio"], output, play, "wav", default_name="speech.wav")


def _emit(
    audio: bytes,
    output: Optional[str],
    play: bool,
    fmt: str,
    *,
    default_name: str,
) -> None:
    """Play, stream to stdout, or write audio bytes to a file."""
    if play:
        _play_audio(audio, fmt)
        return
    if output == "-":
        sys.stdout.buffer.write(audio)
        return
    out_path = Path(output) if output else Path(default_name)
    out_path.write_bytes(audio)
    typer.echo(f"Saved to {out_path}")


def _play_audio(data: bytes, fmt: str) -> None:
    """Pipe audio bytes to the first available player."""
    if shutil.which("aplay"):
        subprocess.run(["aplay", "-q", "-"], input=data, check=False)
        return
    if shutil.which("afplay"):
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            subprocess.run(["afplay", tmp], check=False)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return
    if shutil.which("mpv"):
        subprocess.run(["mpv", "--no-video", "--really-quiet", "-"], input=data, check=False)
        return
    typer.echo("Warning: no audio player found (tried aplay, afplay, mpv)", err=True)
