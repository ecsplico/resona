import sys
import os
import typer

from .engines import engines_app
from .replacements import replacements_app
from .prompts import prompts_app
from .watch import watch_directory
from .transcribe import transcribe_files
from .submit import submit_files
from .speech import speak

app = typer.Typer(help="resona CLI")

app.add_typer(engines_app, name="engines", help="Manage engine server addresses.")
app.add_typer(replacements_app, name="replacements", help="Manage text replacement rules.")
app.add_typer(prompts_app, name="prompts", help="Manage initial transcription prompts.")
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


@app.command()
def live():
    """Launch the live transcription TUI."""
    _require_modules("textual", "sounddevice", "soundfile", "soxr", "resona_asr_core")
    import logging
    from dotenv import load_dotenv
    import sounddevice as sd

    load_dotenv()

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
    WSLiveApp().run()


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
