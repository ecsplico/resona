import re
import logging
from sqlmodel import Session, select

from core.db.models import Replacement
from core.db.engine import engine

log = logging.getLogger('uvicorn.test') # Or use a dedicated logger

def process_markdown(result: dict) -> dict:
    """
    Applies text replacements from the database to the transcribed text
    and adds it to the result dictionary under the 'md' key.

    Args:
        result: The ASR result dictionary, expected to contain a 'text' key.

    Returns:
        The ASR result dictionary, updated with an 'md' key.
    """
    text_to_process = result.get("text")
    if not text_to_process:
        log.warning("Markdown postprocessing: No 'text' found in result. Skipping.")
        result["md"] = "" # Add empty md string if no text
        return result

    try:
        with Session(engine) as session:
            statement = select(Replacement).where(Replacement.active == True).order_by(Replacement.id) # Order by ID for consistency
            replacements = session.exec(statement).all()
            count = 0
            processed_text = text_to_process # Start with original text
            for r in replacements:
                # Use re.sub for case-insensitive replacement
                new_text, num_subs = re.compile(r.name, re.IGNORECASE).subn(r.replacement, processed_text)
                if num_subs > 0:
                    processed_text = new_text
                    count += num_subs
            if count > 0:
                log.info(f"Applied {count} markdown replacements.")
            result["md"] = processed_text
    except Exception as e:
        log.error(f"Error fetching or applying replacements for markdown: {e}", exc_info=True)
        result["md"] = text_to_process # Return original text in 'md' field on error
    return result