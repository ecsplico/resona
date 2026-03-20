import json
import logging
from typing import Dict
from decouple import config

from .db.models import Job

log = logging.getLogger(__name__)


def update_job_attributes_from_result(job: Job, result_data: Dict):
    """Update the attributes of a Job object based on the engine transcription result."""
    job.transcript = result_data.get("text", "")
    job.language = result_data.get("language", "")

    try:
        segments = result_data.get("segments", [])
        serializable_segments = [
            segment if isinstance(segment, dict) else segment._asdict()
            for segment in segments
        ]
        job.segments = json.dumps(serializable_segments)
    except AttributeError:
        log.warning(f"Could not serialize segments for job {job.id}.")
        job.segments = "[]"
    except Exception as e:
        log.error(f"Unexpected error serializing segments for job {job.id}: {e}", exc_info=True)
        job.segments = "[]"

    job.transcribed = True
    job.model = config("ASR_MODEL_NAME", default="unknown")
    job.md = result_data.get("md", "")

    log.info(f"Updated attributes for job {job.id} from result data.")
