import re
import logging
from datetime import datetime

from .paths import MD_PATH

log = logging.getLogger(__name__)


def write_md_file(id: int, filename: str, md: str, keepfile: bool,
                  structured: str | None = None):
    """Write a transcription result to a markdown file with metadata.

    When `structured` is given (a JSON string), also write it as a `.json`
    sidecar beside the `.md` file (same name, `.json` extension).
    """
    p_match = re.compile(r"[Dd]okumentation von ([^\s]*)").search(md)
    patient = p_match.group(1) if p_match else ""

    filepart = id if not patient else patient
    date = datetime.now().strftime("%Y-%m-%d")
    date_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_filename = f"{MD_PATH}/{date} {filepart} ({id}).md"
    log.info(f"Writing markdown file: {md_filename}")

    try:
        with open(md_filename, "w", encoding='utf-8') as file:
            file.write(f"---\n")
            file.write(f"created: {date_full}\n")
            file.write(f"patient: {patient}\n")
            file.write(f"diktiert: true\n")
            file.write(f"ready: false\n")
            file.write(f"nexus: false\n")
            file.write(f"---\n\n")
            if keepfile and filename:
                file.write(f"Audio: \n![[{filename}]]\n\n")
            file.write(md)
        if structured:
            json_filename = md_filename[:-3] + ".json"
            with open(json_filename, "w", encoding="utf-8") as jf:
                jf.write(structured)
    except IOError as e:
        log.error(f"Error writing markdown file {md_filename}: {e}")
