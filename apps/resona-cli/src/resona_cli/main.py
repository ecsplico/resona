import sys
import os
import typer

from .backends import backends_app
from .replacements import replacements_app
from .prompts import prompts_app
from .watch import watch_directory
from .transcribe import transcribe_files

app = typer.Typer(help="resona CLI")

app.add_typer(backends_app, name="backends", help="Manage backend server addresses.")
app.add_typer(replacements_app, name="replacements", help="Manage text replacement rules.")
app.add_typer(prompts_app, name="prompts", help="Manage initial transcription prompts.")
app.command("watch")(watch_directory)
app.command("transcribe")(transcribe_files)


def _check_missing(modules):
    """Return list of modules whose import spec cannot be found."""
    import importlib.util
    return [m for m in modules if importlib.util.find_spec(m) is None]


def _require_extra(extra: str, *modules: str) -> None:
    """Check each module is installable; raise typer.Exit with install hint on failure."""
    missing = _check_missing(modules)
    if missing:
        typer.echo(
            f"Missing dependencies for this command: {', '.join(missing)}.\n"
            f"Install with:  uv tool install 'resona-cli[{extra}]'\n"
            f"or:            pip install 'resona-cli[{extra}]'",
            err=True,
        )
        raise typer.Exit(2)


@app.command()
def rec():
    """Launch the audio recorder TUI."""
    _require_extra("record", "textual", "sounddevice", "soundfile")
    from .micrec import run_mic_rec_app
    run_mic_rec_app()


@app.command()
def live():
    """Launch the live transcription TUI."""
    _require_extra("live", "textual", "sounddevice", "soundfile", "torchaudio", "resona_asr_core")
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
    _require_extra("record", "textual", "sounddevice", "soundfile")
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
