import typer

from .replacements import replacements_app # Import the replacements app
from .prompts import prompts_app # Import the prompts app

app = typer.Typer(help="Manage database entries for replacements and initial prompts.")

# Add the replacements commands
app.add_typer(replacements_app, name="replacements", help="Manage replacement rules.")
app.add_typer(prompts_app, name="prompts", help="Manage the initial prompt.")

if __name__ == "__main__":
    app()