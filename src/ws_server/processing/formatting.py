import re
import logging
from typing import TextIO, Union
from datetime import datetime
from sqlmodel import Session, select
from whisper.utils import ResultWriter, WriteTXT, WriteSRT, WriteVTT, WriteTSV, WriteJSON

# Import necessary components from new locations
from ..db.models import Replacement
from ..db.engine import engine # Assuming engine is defined in db/engine.py
from ..core.paths import MD_PATH # Assuming MD_PATH is defined in core/paths.py

log = logging.getLogger('uvicorn.test') # Or use a dedicated logger

def write_result(
        result: dict, file: TextIO, output: Union[str, None]
):
    """Writes the transcription result to a file in the specified format."""
    # Original options, consider making them configurable
    options = {
        'max_line_width': 1000,
        'max_line_count': 10,
        'highlight_words': False
    }
    # Using ResultWriter subclasses directly
    if output == "srt":
        WriteSRT(ResultWriter).write_result(result, file=file, options=options)
    elif output == "vtt":
        WriteVTT(ResultWriter).write_result(result, file=file, options=options)
    elif output == "tsv":
        WriteTSV(ResultWriter).write_result(result, file=file, options=options)
    elif output == "json":
        WriteJSON(ResultWriter).write_result(result, file=file, options=options)
    elif output == "txt":
        WriteTXT(ResultWriter).write_result(result, file=file, options=options)
    else:
        log.warning(f"Invalid output format specified: {output}. Defaulting to text.")
        WriteTXT(ResultWriter).write_result(result, file=file, options=options)
        # Or raise an error: raise ValueError("Invalid output format specified")


def write_md_file(id: int, filename:str, md:str, keepfile:bool ):
    """Writes the transcription result to a markdown file with metadata."""
    # Try to find name of patient using regex
    p_match = re.compile(r"[Dd]okumentation von ([^\s]*)").search(md)
    patient = p_match.group(1) if p_match else ""

    filepart = id if not patient else patient
    date = datetime.now().strftime("%Y-%m-%d")
    date_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Construct markdown file path
    md_filename = f"{MD_PATH}/{date} {filepart} ({id}).md"
    log.info(f"Writing markdown file: {md_filename}")

    try:
        with open(md_filename, "w", encoding='utf-8') as file: # Specify encoding
            # Write YAML frontmatter
            file.write(f"---\n")
            file.write(f"created: {date_full}\n")
            file.write(f"patient: {patient}\n")
            file.write(f"diktiert: true\n")
            file.write(f"ready: false\n")
            file.write(f"nexus: false\n") # Assuming these flags are standard
            file.write(f"---\n\n")
            # Include audio file link if kept
            if keepfile and filename: # Check if filename is not empty
                file.write(f"Audio: \n![[{filename}]]\n\n")
            # Write the main markdown content
            file.write(md)
    except IOError as e:
        log.error(f"Error writing markdown file {md_filename}: {e}")


def toMarkdown(text: str) -> str:
    """Applies text replacements from the database to the input text."""
    # Get active replacements from the database
    try:
        with Session(engine) as session:
            statement = select(Replacement).where(Replacement.active == True).order_by(Replacement.id) # Order by ID for consistency
            replacements = session.exec(statement).all()
            count = 0
            for r in replacements:
                # Use re.sub for case-insensitive replacement
                new_text, num_subs = re.compile(r.name, re.IGNORECASE).subn(r.replacement, text)
                if num_subs > 0:
                    text = new_text
                    count += num_subs
                    # log.debug(f"Replaced '{r.name}' with '{r.replacement}' ({num_subs} times)")
            if count > 0:
                log.info(f"Applied {count} markdown replacements.")
            return text
    except Exception as e:
        log.error(f"Error fetching or applying replacements: {e}")
        return text # Return original text on error