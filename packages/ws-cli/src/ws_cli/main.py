import typer

from .backends import backends_app
from .replacements import replacements_app
from .prompts import prompts_app
from .watch import watch_directory
from .batch import batch_transcribe

app = typer.Typer(help="whisper-server CLI — manage transcription jobs, replacements, and prompts.")

app.add_typer(backends_app, name="backends", help="Manage backend server addresses.")
app.add_typer(replacements_app, name="replacements", help="Manage text replacement rules.")
app.add_typer(prompts_app, name="prompts", help="Manage initial transcription prompts.")
app.command("watch")(watch_directory)
app.command("batch")(batch_transcribe)


if __name__ == "__main__":
    app()
