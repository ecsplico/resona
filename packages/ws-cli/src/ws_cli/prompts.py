import typer
import logging
from ws_client.client import WhisperClient

log = logging.getLogger(__name__)
prompts_app = typer.Typer(
    help="Manage the initial prompt used for transcription.",
    no_args_is_help=True,
)


def _client() -> WhisperClient:
    return WhisperClient()


@prompts_app.command("add")
def add_prompt(
    prompt_text: str = typer.Argument(..., help="The initial prompt phrase to add.")
):
    """Add a new initial prompt via the API."""
    try:
        p = _client().add_prompt(prompt_text)
        print(f"Added prompt ID {p['id']}: '{p['phrase']}'")
    except Exception as e:
        print(f"Error adding prompt: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("list")
def list_prompts():
    """List all initial prompts."""
    try:
        prompts = _client().list_prompts()
        if not prompts:
            print("No prompts found.")
            return
        print("Initial prompts:")
        for p in prompts:
            active = "Active" if p.get("active") else "Inactive"
            print(f"  ID={p['id']} [{active}] '{p['phrase']}'")
    except Exception as e:
        print(f"Error listing prompts: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("activate")
def activate_prompt(
    prompt_id: int = typer.Argument(..., help="ID of the prompt to activate.")
):
    """Activate a prompt (deactivates all others)."""
    try:
        _client().activate_prompt(prompt_id)
        print(f"Activated prompt ID {prompt_id}")
    except Exception as e:
        print(f"Error activating prompt: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("deactivate")
def deactivate_prompt(
    prompt_id: int = typer.Argument(..., help="ID of the prompt to deactivate.")
):
    """Deactivate a prompt."""
    try:
        _client().deactivate_prompt(prompt_id)
        print(f"Deactivated prompt ID {prompt_id}")
    except Exception as e:
        print(f"Error deactivating prompt: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("remove")
def remove_prompt(
    prompt_id: int = typer.Argument(..., help="ID of the prompt to remove.")
):
    """Remove a prompt by ID."""
    try:
        _client().remove_prompt(prompt_id)
        print(f"Removed prompt ID {prompt_id}")
    except Exception as e:
        print(f"Error removing prompt: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    prompts_app()
