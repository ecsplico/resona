import typer
from sqlmodel import Session, select, delete
import logging

from ws_server.db.engine import engine
from ws_server.db.models import InitialPrompt

log = logging.getLogger(__name__)

prompts_app = typer.Typer(
    help="Manage the initial prompt used for transcription.",
    no_args_is_help=True,
)

@prompts_app.command("set")
def set_initial_prompt(
    prompt_text: str = typer.Argument(..., help="The initial prompt phrase to set.")
):
    """
    Sets or replaces the initial prompt in the database.

    This command deletes any existing initial prompts and adds the new one.
    """
    try:
        with Session(engine) as session:
            # Delete existing prompts
            delete_statement = delete(InitialPrompt)
            session.exec(delete_statement) # type: ignore

            # Add the new prompt
            new_prompt = InitialPrompt(phrase=prompt_text, active=True)
            session.add(new_prompt)
            session.commit()
            log.info(f"Initial prompt set to: '{prompt_text}'")
            print(f"✅ Initial prompt set successfully.")
    except Exception as e:
        log.error(f"Error setting initial prompt: {e}", exc_info=True)
        print(f"❌ Error setting initial prompt: {e}")
        raise typer.Exit(code=1)

@prompts_app.command("show")
def show_initial_prompt():
    """
    Displays the currently set initial prompt.
    """
    try:
        with Session(engine) as session:
            statement = select(InitialPrompt).where(InitialPrompt.active == True)
            # Fetch the first active prompt, assuming only one should be active
            prompt = session.exec(statement).first()

            if prompt:
                print(f"Current initial prompt: '{prompt.phrase}'")
            else:
                print("ℹ️ No initial prompt is currently set.")
    except Exception as e:
        log.error(f"Error showing initial prompt: {e}", exc_info=True)
        print(f"❌ Error showing initial prompt: {e}")
        raise typer.Exit(code=1)

@prompts_app.command("clear")
def clear_initial_prompt():
    """
    Removes the initial prompt from the database.
    """
    try:
        with Session(engine) as session:
            # Delete all prompts (active or not)
            delete_statement = delete(InitialPrompt)
            result = session.exec(delete_statement) # type: ignore
            session.commit()

            deleted_count = result.rowcount
            if deleted_count > 0:
                log.info(f"Cleared {deleted_count} initial prompt(s).")
                print(f"✅ Initial prompt cleared successfully.")
            else:
                log.info("No initial prompt found to clear.")
                print("ℹ️ No initial prompt was set, nothing to clear.")
    except Exception as e:
        log.error(f"Error clearing initial prompt: {e}", exc_info=True)
        print(f"❌ Error clearing initial prompt: {e}")
        raise typer.Exit(code=1)

if __name__ == "__main__":
    prompts_app()