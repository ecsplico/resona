import logging
import os
import re
import secrets
from typing import Union, Annotated, List
from io import StringIO
from pathlib import Path
import aiofiles
from decouple import config
from fastapi import APIRouter, File, Query, UploadFile, HTTPException, status, Body, Form, Depends
from sqlmodel import Session, select
from fastapi.responses import StreamingResponse

# Import necessary components from other modules
from core.db.models import Job, JobStatus
from core.db.engine import engine
from core.db.utils import register_job
from core.paths import FILE_PATH
from ..processing.utils import run_asr
from ..processing.formatting import write_result
from .auth import verify_api_key

# Get logger and config
logger = logging.getLogger(__name__)
MODE = config("ASR_MODE")

# Create API router
router = APIRouter()


def get_db_session():
    """Dependency for database session management."""
    with Session(engine) as session:
        yield session


def sanitize_filename(filename: str) -> str:
    """
    Sanitizes a filename to prevent path traversal attacks.
    
    Args:
        filename: The original filename.
    
    Returns:
        A sanitized filename safe for storage.
    """
    # Handle both Unix and Windows path separators
    # Get just the basename (no path components)
    filename = os.path.basename(filename)
    # Also handle Windows backslash paths that basename might miss
    if '\\' in filename:
        filename = filename.split('\\')[-1]
    
    # Remove any non-alphanumeric characters except dots, dashes, and underscores
    filename = re.sub(r'[^\w\-.]', '_', filename)
    
    # Ensure filename is not empty, just dots, or parent directory notation
    if not filename or filename in ['.', '..'] or filename.startswith('.'):
        filename = 'unnamed_file'
    
    return filename


def validate_audio_file(file: UploadFile) -> None:
    """
    Validates that an uploaded file is a supported audio format.
    
    Args:
        file: The uploaded file to validate.
    
    Raises:
        HTTPException: If the file type is not supported.
    """
    allowed_types = ['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/ogg']
    allowed_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac']
    
    # Check content type
    if file.content_type not in allowed_types:
        # Also check file extension as a fallback
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"Unsupported file type. Allowed types: {', '.join(allowed_extensions)}"
                )


##### Direct processing endpoints

@router.post("/asr", tags=["Endpoints"])
async def asr(
        audio_file: UploadFile = File(...),
        encode: Union[bool, None] = Query(default=True, description="Encode audio first through ffmpeg"),
        task: Union[str, None] = Query(default="transcribe", enum=["transcribe", "translate"]),
        language: Union[str, None] = Query(default=None),
        initial_prompt: Union[str, None] = Query(default=None),
        vad_filter: Annotated[bool | None, Query(
                description="Enable the voice activity detection (VAD) to filter out parts of the audio without speech",
                include_in_schema=(True if MODE == "faster_whisper" else False)
            )] = False,
        word_timestamps: bool = Query(default=False, description="Word level timestamps"),
        markdown: bool = Query(default=True, description="Convert the result to markdown"),
        output: Union[str, None] = Query(default="txt", enum=["txt", "vtt", "srt", "tsv", "json"]),
        api_key: str = Depends(verify_api_key)
):
    """Process an audio file synchronously and return the transcription result."""
    logger.info("Processing audio in asr endpoint")
    validate_audio_file(audio_file)
    
    result = run_asr(audio_file.file, task=task, language=language or "de", markdown=markdown)

    # For obsidian Plugin put the markdown in the segments
    if "md" in result:
        result["segments"] = [{"text": result["md"]}]
    else:
        result["segments"] = []

    output_file = StringIO()
    write_result(result, output_file, output)
    output_file.seek(0)

    safe_filename = sanitize_filename(audio_file.filename or "transcript")
    
    return StreamingResponse(
        output_file,
        media_type="text/plain",
        headers={
            'Asr-Engine': MODE,
            'Content-Disposition': f'attachment; filename="{safe_filename}.{output}"'
        })


##### Indirect processing endpoints

@router.post(
    "/asr-async",
    summary="Asynchronously process uploaded audio files using whisper",
    tags=["ASR"]
)
async def transcribe_async(
    audio_files: List[UploadFile] = File(...),
    keep: bool = Form(True),
    translate: bool = Form(False),
    api_key: str = Depends(verify_api_key)
):
    """
    Upload one or more files and register them for asynchronous processing using whisper.
    """
    jobs = []
    for audio_file in audio_files:
        validate_audio_file(audio_file)
        
        name_original = audio_file.filename or "unnamed"
        safe_name = sanitize_filename(name_original)
        extension = Path(safe_name).suffix or '.bin'
        name_new = f"{secrets.token_hex(10)}{extension}"

        file_path = Path(FILE_PATH) / name_new
        async with aiofiles.open(file_path, "wb") as buffer:
            content = await audio_file.read()
            await buffer.write(content)
        
        job = register_job(filename=name_new, upload_name=name_original, keep=keep, translate=translate)
        jobs.append(job)
    
    return jobs


@router.post(
    "/asr-registerfile",
    summary="Register an existing filename for processing (i.e. to reprocess)",
    tags=["Job"]
)
async def registerfile(
    filename: str = Body(...),
    api_key: str = Depends(verify_api_key)
):
    """
    Register a file for asynchronous processing.
    """
    # Validate that file exists
    file_path = Path(FILE_PATH) / filename
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{filename}' not found"
        )
    
    job = register_job(filename=filename, upload_name=filename, keep=True, translate=False)
    return job


##### Serve the results

@router.get(
    "/job/{job_id}",
    summary="Get the result of an asynchronous processing job",
    tags=["Job"]
)
def get_job(
    job_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Retrieve job status and results by job ID."""
    statement = select(Job).where(Job.id == job_id)
    job = session.exec(statement).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    return job


@router.get(
    "/jobs/",
    summary="Get all jobs",
    tags=["Job"]
)
def get_jobs(
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Retrieve all transcription jobs."""
    statement = select(Job)
    jobs = session.exec(statement).all()
    return jobs