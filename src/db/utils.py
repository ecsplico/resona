import logging
from sqlmodel import Session, select

# Import necessary components from new locations
from .engine import engine
from .models import Job, Replacement
from ..core.replacements import replacements as default_replacements # Import the raw data

log = logging.getLogger(__name__)

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

def register_job(filename: str, upload_name: str, keep: bool = True, translate: bool = False) -> dict:
    """
    Registers a new transcription job in the database.

    Args:
        filename: The unique filename assigned for storage.
        upload_name: The original uploaded filename (for reference, though not stored in Job model currently).
        keep: Whether to keep the audio file after processing.
        translate: Whether the job includes translation.

    Returns:
        A dictionary containing the new job's ID and related API paths.
    """
    log.info(f"Registering job: filename='{filename}', keep={keep}, translate={translate}")
    try:
        with Session(engine) as session:
            # Create a new Job instance
            job = Job(filename=filename, keepfile=keep, translate=translate)
            session.add(job)
            session.commit()
            session.refresh(job) # Refresh to get the assigned ID

            log.info(f"Job registered successfully with ID: {job.id}")
            # Prepare response dictionary
            res = {
                "id": job.id,
                "file": f"/files/{job.filename}", # Path to access the stored file via API
                "result": f"/job/{job.id}",      # Path to get the job status/result via API
            }
            return res
    except Exception as e:
        # Ensure this block uses 4 spaces for indentation
        log.error(f"Error registering job for file {filename}: {e}", exc_info=True)
        # Handle error appropriately, maybe raise a custom exception
        raise RuntimeError(f"Failed to register job for file {filename}") from e

# Note: The actual calls to populate_default_replacements() and create_db_and_tables()
# should happen during application startup (e.g., in lifespan).