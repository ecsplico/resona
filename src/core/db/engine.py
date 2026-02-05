import logging
import os
import sys
from pathlib import Path
from sqlmodel import SQLModel, create_engine, select, Session

# Import necessary components from new locations
from .models import Job, Replacement, InitialPrompt # Add InitialPrompt here
from core.presets import replacements as default_replacements # Import the raw data
from core.presets import initial_prompts # Import the initial prompts data
# Import DATABASE_URL from its new location
from core.paths import DATABASE_URL, DB_PATH

log = logging.getLogger("uvicorn.test") # Use a logger specific to this module

def validate_database_directory():
    """
    Validates that the database directory exists and is writable.
    Provides clear error messages if validation fails.
    """
    db_dir = Path(DB_PATH)
    
    # Check if directory exists
    if not db_dir.exists():
        try:
            log.info(f"Creating database directory: {db_dir}")
            db_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            error_msg = (
                f"\n{'='*60}\n"
                f"ERROR: Cannot create database directory\n"
                f"{'='*60}\n"
                f"Location: {db_dir}\n"
                f"Reason: Permission denied\n\n"
                f"Solution: Run one of the following commands:\n"
                f"  sudo mkdir -p {db_dir}\n"
                f"  sudo chown $USER:$USER {db_dir}\n"
                f"\nOr set a different DB_PATH in your .env file.\n"
                f"{'='*60}\n"
            )
            log.error(error_msg)
            print(error_msg, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            error_msg = (
                f"\n{'='*60}\n"
                f"ERROR: Cannot create database directory\n"
                f"{'='*60}\n"
                f"Location: {db_dir}\n"
                f"Reason: {str(e)}\n\n"
                f"Solution: Check the path and permissions.\n"
                f"{'='*60}\n"
            )
            log.error(error_msg)
            print(error_msg, file=sys.stderr)
            sys.exit(1)
    
    # Check if directory is writable
    if not os.access(db_dir, os.W_OK):
        error_msg = (
            f"\n{'='*60}\n"
            f"ERROR: Database directory is not writable\n"
            f"{'='*60}\n"
            f"Location: {db_dir}\n"
            f"Reason: Insufficient permissions\n\n"
            f"Solution: Run the following command:\n"
            f"  sudo chown $USER:$USER {db_dir}\n"
            f"\nOr set a different DB_PATH in your .env file.\n"
            f"{'='*60}\n"
        )
        log.error(error_msg)
        print(error_msg, file=sys.stderr)
        sys.exit(1)
    
    log.info(f"Database directory validated: {db_dir}")

# Validate directory before creating engine
validate_database_directory()

log.info(f"Creating database engine for URL: {DATABASE_URL}")
# Create the database engine instance
engine = create_engine(DATABASE_URL) # echo=True for SQL logging


def create_db_and_tables():
    """Creates all tables defined by SQLModel metadata."""
    log.info("Creating database tables...")
    try:
        # This requires all models that inherit from SQLModel to be imported
        # somewhere before this function is called. Often done in models.py or __init__.py.
        # Make sure Job and Replacement models are imported before calling this.
        SQLModel.metadata.create_all(engine)
        log.info("Database tables created successfully (if they didn't exist).")
    except Exception as e:
        log.error(f"Error creating database tables: {e}", exc_info=True)
        raise # Re-raise the exception to indicate failure

# Note: The actual call to create_db_and_tables() should happen
# during application startup, perhaps in the lifespan manager in api/app.py
# or in run.py, after ensuring models are imported.
# Avoid calling it directly at module level here.


def populate_default_replacements():
    """Checks if the Replacement table is empty and populates it with defaults."""
    log.info("Checking database for default replacements...")
    try:
        with Session(engine) as session:
            statement = select(Replacement)
            existing_replacements = session.exec(statement).first() # Check if at least one exists

            if existing_replacements is None:
                log.info("No replacements found. Populating database with defaults...")
                count = 0
                for name, replacement_text in default_replacements:
                    # Ensure replacement_text is a string, handle potential non-string data
                    if isinstance(replacement_text, str):
                         db_replacement = Replacement(name=name, replacement=replacement_text, active=True)
                         session.add(db_replacement)
                         count += 1
                    else:
                         log.warning(f"Skipping non-string replacement for '{name}': {replacement_text}")

                session.commit()
                log.info(f"Added {count} default replacements to the database.")
            else:
                log.info("Replacements table already populated.")
    except Exception as e:
        log.error(f"Error during replacement population check: {e}", exc_info=True)
        # Depending on severity, you might want to raise this or handle it
def populate_initial_prompts():
    """Checks if the InitialPrompt table is empty and populates it with defaults."""
    log.info("Checking database for initial prompts...")
    try:
        with Session(engine) as session:
            statement = select(InitialPrompt)
            existing_prompts = session.exec(statement).first() # Check if at least one exists

            if existing_prompts is None:
                log.info("No initial prompts found. Populating database with defaults...")
                count = 0
                for prompt_text in initial_prompts:
                    # InitialPrompt model uses 'phrase' field
                    db_prompt = InitialPrompt(phrase=prompt_text, active=True) # Assuming an 'active' field similar to Replacement
                    session.add(db_prompt)
                    count += 1

                session.commit()
                log.info(f"Added {count} initial prompts to the database.")
            else:
                log.info("Initial prompts table already populated.")
    except Exception as e:
        log.error(f"Error during initial prompt population check: {e}", exc_info=True)
        # Depending on severity, you might want to raise this or handle it

