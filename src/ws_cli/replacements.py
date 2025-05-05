import typer
from sqlmodel import Session, select
import logging

from core.db.engine import engine # Import engine directly
from core.db.models import Replacement

log = logging.getLogger(__name__)
replacements_app = typer.Typer()

@replacements_app.command("add")
def add_replacement(
    pattern: str = typer.Argument(..., help="The pattern to replace."),
    replacement_text: str = typer.Argument(..., help="The text to replace the pattern with.")
):
    """
    Adds a new replacement rule to the database.
    """
    try:
        with Session(engine) as session:
            # Check if pattern already exists
            statement = select(Replacement).where(Replacement.name == pattern)
            existing = session.exec(statement).first()
            if existing:
                print(f"Error: Pattern '{pattern}' already exists.")
                raise typer.Exit(code=1)

            db_replacement = Replacement(name=pattern, replacement=replacement_text, active=True)
            session.add(db_replacement)
            session.commit()
            print(f"Successfully added replacement: '{pattern}' -> '{replacement_text}'")
    except Exception as e:
        log.error(f"Error adding replacement '{pattern}': {e}", exc_info=True)
        print(f"Error adding replacement: {e}")
        raise typer.Exit(code=1)

@replacements_app.command("list")
def list_replacements():
    """
    Lists all replacement rules from the database.
    """
    try:
        with Session(engine) as session:
            statement = select(Replacement)
            results = session.exec(statement).all()
            if not results:
                print("No replacements found in the database.")
                return

            print("Current Replacements:")
            print("-" * 30)
            for rep in results:
                status = "[Active]" if rep.active else "[Inactive]"
                print(f"- {status} '{rep.name}' -> '{rep.replacement}' (ID: {rep.id})")
            print("-" * 30)

    except Exception as e:
        log.error(f"Error listing replacements: {e}", exc_info=True)
        print(f"Error listing replacements: {e}")
        raise typer.Exit(code=1)

@replacements_app.command("delete")
def delete_replacement(
    pattern: str = typer.Argument(..., help="The pattern of the replacement rule to delete.")
):
    """
    Deletes a replacement rule from the database by its pattern.
    """
    try:
        with Session(engine) as session:
            statement = select(Replacement).where(Replacement.name == pattern)
            replacement_to_delete = session.exec(statement).first()

            if replacement_to_delete is None:
                print(f"Error: Replacement pattern '{pattern}' not found.")
                raise typer.Exit(code=1)

            session.delete(replacement_to_delete)
            session.commit()
            print(f"Successfully deleted replacement: '{pattern}'")
    except Exception as e:
        log.error(f"Error deleting replacement '{pattern}': {e}", exc_info=True)
        print(f"Error deleting replacement: {e}")
        raise typer.Exit(code=1)

if __name__ == "__main__":
    replacements_app()