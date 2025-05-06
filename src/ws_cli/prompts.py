import typer
from sqlmodel import Session, select, delete
import logging

from core.db.engine import engine
from core.db.models import InitialPrompt

log = logging.getLogger(__name__)

prompts_app = typer.Typer(
    help="Manage the initial prompt used for transcription.",
    no_args_is_help=True,
)

@prompts_app.command("add")
def add_prompt(
    prompt_text: str = typer.Argument(..., help="The initial prompt phrase to add.")
):
    """
    Adds a new initial prompt to the database as active.
    """
    try:
        with Session(engine) as session:
            # Check if prompt already exists
            statement = select(InitialPrompt).where(InitialPrompt.phrase == prompt_text)
            existing_prompt = session.exec(statement).first()
            if existing_prompt:
                print(f"ℹ️ Prompt '{prompt_text}' already exists with ID {existing_prompt.id}.")
                return

            new_prompt = InitialPrompt(phrase=prompt_text, active=True)
            session.add(new_prompt)
            session.commit()
            session.refresh(new_prompt)
            log.info(f"Added initial prompt: '{prompt_text}' with ID {new_prompt.id}")
            print(f"✅ Initial prompt '{prompt_text}' added successfully with ID {new_prompt.id}. It is now active.")
    except Exception as e:
        log.error(f"Error adding initial prompt: {e}", exc_info=True)
        print(f"❌ Error adding initial prompt: {e}")
        raise typer.Exit(code=1)

@prompts_app.command("list")
def list_prompts():
    """
    Lists all initial prompts in the database with their status.
    """
    try:
        with Session(engine) as session:
            statement = select(InitialPrompt).order_by(InitialPrompt.id)
            prompts = session.exec(statement).all()

            if not prompts:
                print("ℹ️ No initial prompts found in the database.")
                return

            print("Available initial prompts:")
            for prompt in prompts:
                status = "✅ Active" if prompt.active else "❌ Inactive"
                print(f"  ID: {prompt.id} | Status: {status} | Prompt: '{prompt.phrase}'")

    except Exception as e:
        log.error(f"Error listing initial prompts: {e}", exc_info=True)
        print(f"❌ Error listing initial prompts: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("remove")
def remove_prompt(
    prompt_id: int = typer.Argument(..., help="The ID of the prompt to remove.")
):
    """
    Removes an initial prompt from the database by its ID.
    """
    try:
        with Session(engine) as session:
            prompt = session.get(InitialPrompt, prompt_id)
            if not prompt:
                print(f"❌ Error: Prompt with ID {prompt_id} not found.")
                raise typer.Exit(code=1)

            session.delete(prompt)
            session.commit()
            log.info(f"Removed initial prompt with ID {prompt_id}: '{prompt.phrase}'")
            print(f"✅ Initial prompt with ID {prompt_id} removed successfully.")
    except Exception as e:
        log.error(f"Error removing initial prompt: {e}", exc_info=True)
        print(f"❌ Error removing initial prompt: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("activate")
def activate_prompt(
    prompt_id: int = typer.Argument(..., help="The ID of the prompt to activate.")
):
    """
    Activates an initial prompt by its ID. Deactivates all other prompts.
    """
    try:
        with Session(engine) as session:
            # Deactivate all existing prompts first
            all_prompts = session.exec(select(InitialPrompt)).all()
            for p in all_prompts:
                if p.active:
                    p.active = False
                    session.add(p)

            # Activate the target prompt
            prompt_to_activate = session.get(InitialPrompt, prompt_id)
            if not prompt_to_activate:
                print(f"❌ Error: Prompt with ID {prompt_id} not found.")
                # Rollback deactivation of others if target not found? Or commit deactivation?
                # Let's commit the deactivation for simplicity, ensuring no prompt is active.
                session.commit()
                raise typer.Exit(code=1)

            prompt_to_activate.active = True
            session.add(prompt_to_activate)
            session.commit()
            log.info(f"Activated initial prompt with ID {prompt_id}: '{prompt_to_activate.phrase}'")
            print(f"✅ Initial prompt with ID {prompt_id} activated successfully. All other prompts deactivated.")
    except Exception as e:
        log.error(f"Error activating initial prompt: {e}", exc_info=True)
        print(f"❌ Error activating initial prompt: {e}")
        raise typer.Exit(code=1)


@prompts_app.command("deactivate")
def deactivate_prompt(
    prompt_id: int = typer.Argument(..., help="The ID of the prompt to deactivate.")
):
    """
    Deactivates an initial prompt by its ID.
    """
    try:
        with Session(engine) as session:
            prompt = session.get(InitialPrompt, prompt_id)
            if not prompt:
                print(f"❌ Error: Prompt with ID {prompt_id} not found.")
                raise typer.Exit(code=1)

            if not prompt.active:
                print(f"ℹ️ Prompt with ID {prompt_id} is already inactive.")
                return

            prompt.active = False
            session.add(prompt)
            session.commit()
            log.info(f"Deactivated initial prompt with ID {prompt_id}: '{prompt.phrase}'")
            print(f"✅ Initial prompt with ID {prompt_id} deactivated successfully.")
    except Exception as e:
        log.error(f"Error deactivating initial prompt: {e}", exc_info=True)
        print(f"❌ Error deactivating initial prompt: {e}")
        raise typer.Exit(code=1)
        log.error(f"Error listing initial prompts: {e}", exc_info=True)
        print(f"❌ Error listing initial prompts: {e}")
        raise typer.Exit(code=1)
# Placeholder for new commands

if __name__ == "__main__":
    prompts_app()