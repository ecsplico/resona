import sys
import os
from typing import Optional

import typer

from .engines import engines_app
from .profiles import profiles_app
from .watch import watch_directory
from .transcribe import transcribe_files
from .submit import submit_files
from .speech import speak

app = typer.Typer(help="resona CLI")

app.add_typer(engines_app, name="engines", help="Manage engine server addresses.")
app.add_typer(profiles_app, name="profiles", help="Manage postprocessing profiles.")
app.command("watch")(watch_directory)
app.command("transcribe")(transcribe_files)
app.command("submit")(submit_files)
app.command("speech")(speak)


def _check_missing(modules):
    """Return list of modules whose import spec cannot be found."""
    import importlib.util
    return [m for m in modules if importlib.util.find_spec(m) is None]


def _require_modules(*modules: str) -> None:
    """Check each module is importable; raise typer.Exit with a reinstall hint on failure."""
    missing = _check_missing(modules)
    if missing:
        typer.echo(
            f"Missing dependencies for this command: {', '.join(missing)}.\n"
            f"These ship with the base resona-cli install — reinstall it:\n"
            f"  uv tool install --reinstall --from ./apps/resona-cli resona-cli",
            err=True,
        )
        raise typer.Exit(2)


@app.command()
def rec():
    """Launch the audio recorder TUI."""
    _require_modules("textual", "sounddevice", "soundfile")
    from .micrec import run_mic_rec_app
    run_mic_rec_app()


_ENGINE_EXTRA = {
    "whisper": "whisper", "voxtral": "voxtral", "mlx-whisper": "mlx",
    "whisper-cpp": "whisper-cpp", "lightning-mlx": "lightning-mlx", "parakeet": "parakeet",
}


def _resolve_live_engine(engine: Optional[str]) -> str:
    """Pick the in-process engine for `resona live`: flag > RESONA_ENGINE > platform default.

    Validates the engine is installed and exports RESONA_ENGINE so the
    in-process transcriber singleton loads it. Exits with an install hint when
    the requested engine is not available.
    """
    from resona_asr_core.registry import installed_engines, recommended_engine

    installed = installed_engines()
    selected = engine or os.getenv("RESONA_ENGINE") or recommended_engine()
    if selected not in installed:
        hint = _ENGINE_EXTRA.get(selected)
        extra = (
            f"\nInstall it with:  uv tool install --reinstall --from ./apps/resona-cli "
            f"'resona-cli[{hint}]'" if hint else ""
        )
        typer.echo(
            f"Engine {selected!r} is not installed. Available: {', '.join(installed)}.{extra}",
            err=True,
        )
        raise typer.Exit(2)
    os.environ["RESONA_ENGINE"] = selected
    return selected


@app.command()
def live(
    language: str = typer.Option("de", "--language", "-l", help="Transcription language (e.g. de, en)."),
    engine: Optional[str] = typer.Option(None, "--engine", "-e",
        help="Local engine to run in-process (default: platform best — MLX on Apple Silicon, else faster-whisper)."),
    remote: Optional[str] = typer.Option(None, "--remote", "-r",
        help="Stream to a remote server instead of running locally. Without --engine: "
             "an engine-server /ws/live (e.g. ws://host:7001). With --engine: a resona-api "
             "/v1/listen gateway (e.g. http://host:7000), where --engine picks the backend "
             "(deepgram, elevenlabs, or a local engine name)."),
):
    """Launch the live transcription TUI."""
    _require_modules("textual", "sounddevice", "soundfile", "soxr", "resona_asr_core")
    import logging
    from dotenv import load_dotenv
    import sounddevice as sd

    load_dotenv()

    if remote:
        _require_modules("websockets")
        if engine:
            typer.echo(f"Live (remote gateway): {remote} engine={engine}", err=True)
        else:
            typer.echo(f"Live (remote): {remote}", err=True)
    else:
        selected_engine = _resolve_live_engine(engine)
        typer.echo(f"Live engine: {selected_engine}", err=True)

    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())

    output_dir = os.getenv("FILE_PATH", os.path.join(os.getcwd(), "data", "files"))
    sample_rate = int(os.getenv("SAMPLE_RATE", 44100))
    channels = int(os.getenv("CHANNELS", 1))

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except Exception as e:
            sys.stderr.write(f"Error: Could not create output directory {output_dir}: {e}\n")
            raise typer.Exit(1)

    try:
        sd.check_input_settings(device=None, samplerate=sample_rate, channels=channels)
    except Exception as e:
        sys.stderr.write(f"Error initializing audio input: {e}\n")
        raise typer.Exit(1)

    from .live_ui import WSLiveApp
    WSLiveApp(
        language=language,
        remote=remote,
        remote_engine=engine if remote else None,
    ).run()


@app.command()
def ui():
    """Launch the record-and-transcribe TUI (records, submits job, shows result)."""
    _require_modules("textual", "sounddevice", "soundfile")
    import logging
    from dotenv import load_dotenv
    import sounddevice as sd

    load_dotenv()

    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())

    output_dir = os.getenv("FILE_PATH", os.path.join(os.getcwd(), "data", "files"))
    sample_rate = int(os.getenv("SAMPLE_RATE", 44100))
    channels = int(os.getenv("CHANNELS", 1))

    os.makedirs(output_dir, exist_ok=True)

    try:
        sd.check_input_settings(device=None, samplerate=sample_rate, channels=channels)
    except Exception as e:
        sys.stderr.write(f"Error initializing audio input: {e}\n")
        raise typer.Exit(1)

    from .ui import WSUIApp
    WSUIApp().run()


if __name__ == "__main__":
    app()
