import typer
import logging
from ws_client.client import WhisperClient

log = logging.getLogger(__name__)
replacements_app = typer.Typer(no_args_is_help=True)


def _client() -> WhisperClient:
    return WhisperClient()


@replacements_app.command("add")
def add_replacement(
    pattern: str = typer.Argument(..., help="The regex pattern to replace."),
    replacement_text: str = typer.Argument(..., help="The text to replace the pattern with.")
):
    """Add a new replacement rule via the API."""
    try:
        r = _client().add_replacement(pattern, replacement_text)
        print(f"Added replacement: '{r['name']}' -> '{r['replacement']}' (ID: {r['id']})")
    except Exception as e:
        print(f"Error adding replacement: {e}")
        raise typer.Exit(code=1)


@replacements_app.command("list")
def list_replacements():
    """List all replacement rules."""
    try:
        replacements = _client().list_replacements()
        if not replacements:
            print("No replacements found.")
            return
        print("Current Replacements:")
        print("-" * 30)
        for r in replacements:
            active = "[Active]" if r.get("active") else "[Inactive]"
            print(f"  {active} ID={r['id']} '{r['name']}' -> '{r['replacement']}'")
    except Exception as e:
        print(f"Error listing replacements: {e}")
        raise typer.Exit(code=1)


@replacements_app.command("delete")
def delete_replacement(
    replacement_id: int = typer.Argument(..., help="ID of the replacement rule to delete.")
):
    """Delete a replacement rule by ID."""
    try:
        _client().delete_replacement(replacement_id)
        print(f"Deleted replacement ID {replacement_id}")
    except Exception as e:
        print(f"Error deleting replacement: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    replacements_app()
