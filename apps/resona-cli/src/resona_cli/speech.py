"""resona speech — synthesise speech from text via the gateway."""
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
        help="Engine name forwarded to the gateway."),
    voice: str = typer.Option("alloy", "--voice", help="Voice name."),
    model: str = typer.Option("tts-1", "--model", help="TTS model name."),
    fmt: str = typer.Option("mp3", "--format",
        help="Output format: mp3, opus, aac, flac."),
    speed: float = typer.Option(1.0, "--speed", help="Speech speed (0.25–4.0)."),
    private: bool = typer.Option(False, "--private",
        help="Require a private engine."),
    play: bool = typer.Option(False, "--play",
        help="Pipe audio to a local player (mpv, ffplay, afplay) instead of saving."),
):
    """Synthesise speech from TEXT via the gateway TTS engine."""
    if play and output == "-":
        typer.echo("Error: --play and --output - are mutually exclusive.", err=True)
        raise typer.Exit(1)

    try:
        client = ResonaClient.from_config(auto_start=False)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    try:
        kwargs: dict = {
            "voice": voice,
            "model": model,
            "response_format": fmt,
            "speed": speed,
            "private": private,
        }
        if engine:
            kwargs["engine"] = engine
        audio = client.create_speech(text, **kwargs)
    except httpx.HTTPStatusError as e:
        typer.echo(f"Error {e.response.status_code}: {e.response.text}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if play:
        _play_audio(audio, fmt)
        return

    if output == "-":
        sys.stdout.buffer.write(audio)
        return

    out_path = Path(output) if output else Path("speech.mp3")
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
            import os
            os.unlink(tmp)
        return
    if shutil.which("mpv"):
        subprocess.run(["mpv", "--no-video", "--really-quiet", "-"], input=data, check=False)
        return
    typer.echo("Warning: no audio player found (tried aplay, afplay, mpv)", err=True)
