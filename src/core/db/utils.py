import logging
from sqlmodel import Session, select

# Import necessary components from new locations
from .engine import engine
from .models import Job, Replacement, InitialPrompt # Add InitialPrompt here
from core.presets import replacements as default_replacements # Import the raw data

log = logging.getLogger(__name__)


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
def get_active_initial_prompts_string() -> str:
    """
    Fetches active initial prompt phrases from the database and returns them as a comma-separated string.
    """
    prompt_string = ""
    try:
        with Session(engine) as session:
            # InitialPrompt is now imported at the top level
            statement = select(InitialPrompt).where(InitialPrompt.active == True)
            active_prompts = session.exec(statement).all()
            phrases = [prompt.phrase for prompt in active_prompts]
            prompt_string = ", ".join(phrases)
            log.info(f"Fetched {len(phrases)} active initial prompts.")
    except Exception as e:
        log.error(f"Error fetching initial prompts from database: {e}", exc_info=True)
    return prompt_string