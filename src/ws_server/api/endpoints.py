import logging
import secrets
from typing import BinaryIO, Union, Annotated, List
from io import StringIO
import aiofiles
from decouple import config
from fastapi import File, Query, UploadFile, HTTPException, status, Body, Form
from sqlmodel import Session, select
from fastapi.responses import StreamingResponse

# Import the app instance from app.py
from .app import app

# Import necessary components from other modules (adjusting paths)
from core.db.models import Job
from core.db.engine import engine # Assuming engine is defined in db/engine.py
from core.db.utils import register_job # Assuming register_job is in db/utils.py
from core.paths import FILE_PATH
from ..processing.utils import run_asr # Assuming run_asr is in processing/utils.py
from ..processing.formatting import write_result # Assuming write_result is in processing/formatting.py

# Get logger and config
logger = logging.getLogger(__name__)
log = logging.getLogger('uvicorn.test') # Assuming uvicorn logger is used
MODE = config("ASR_MODE")

##### Direct processing endpoints

@app.post("/asr", tags=["Endpoints"])
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
        output: Union[str, None] = Query(default="txt", enum=["txt", "vtt", "srt", "tsv", "json"])
):
    # result = transcribe(load_audio(audio_file.file, encode), task, language, initial_prompt, vad_filter, word_timestamps, output)
    logger.info("Processing audio in asr endpoint")
    # Assuming run_asr and write_result are correctly imported
    result = run_asr(audio_file.file, markdown=True)

    # For obsidian Plugin put the markdown in the segments
    # Assuming result structure includes 'md'
    if "md" in result:
        result["segments"] = [{"text": result["md"]}]
    else:
         result["segments"] = [] # Handle case where md might not be present

    # log.info(result)
    output_file = StringIO()
    write_result(result, output_file, output)
    output_file.seek(0)

    return StreamingResponse(
        output_file,
        media_type="text/plain",
        headers = {
            'Asr-Engine': MODE,
            'Content-Disposition': f'attachment; filename="{audio_file.filename}.{output}"'
        })

##### Indirect processing endpoints

@app.post(
    "/asr-async",
    summary="Asyncronous process an uploaded audiofile using whisper",
    tags=["ASR"]
    )
async def transcribe_async(audio_files: List[UploadFile] = File(...), keep: bool = Form(True), translate: bool = Form(False)):
    """
        Upload a File and register it for asynchronous processing using whisper
    """
    jobs = []
    for audio_file in audio_files:
        if (not audio_file.content_type in  ['audio/mpeg', 'audio/wav', 'audio/x-wav']):
            raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Submit an supported file-type.")
        else:
            name_original = audio_file.filename
            extension = name_original.split(".")[1] if '.' in name_original else 'bin' # Safer extension split
            name_new = f"{secrets.token_hex(10)}.{extension}"

            async with aiofiles.open(f"{FILE_PATH}/{name_new}", "wb") as buffer:
                content = await audio_file.read()
                await buffer.write(content)
            # Assuming register_job is correctly imported and takes these args
            job = register_job(filename=name_new, upload_name=name_original, keep=keep, translate=translate)
            jobs.append(job)
    return jobs


@app.post(
    "/asr-registerfile",
    summary="Register an (existing) filename for processing (i.e. to reprocess)",
    tags=["Job"]
    )
async def registerfile(filename: str = Body(...)):
    """
    Register a file for asynchronous processing
    """
    # Assuming register_job can handle just filename
    # Need to decide default values for keep/translate or modify register_job
    job = register_job(filename=filename, upload_name=filename, keep=True, translate=False) # Made assumptions here
    return job

##### Serve the results

@app.get(
    "/job/{id}",
    summary="Get the result of an asynchronous processing",
    tags=["Job"]
    )
def get_async(id : str):
    # Assuming engine is correctly imported
    with Session(engine) as session:
        statement = select(Job).where(Job.id == id)
        job = session.exec(statement).first()
        return job


@app.get(
    "/jobs/",
    summary="Get all Jobs",
    tags=["Job"]
    )
def get_jobs():
    # Assuming engine is correctly imported
    with Session(engine) as session:
        statement = select(Job)
        jobs = session.exec(statement).all()
        return jobs