import logging
import secrets
from threading import Event
from typing import BinaryIO, Union, Annotated, List
from io import StringIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
import logging
import aiofiles
from contextlib import asynccontextmanager
from decouple import config
from fastapi import FastAPI, File, Query, UploadFile, HTTPException, status, Body, Form
from fastapi.staticfiles import StaticFiles
from sqlmodel import Field, Session, create_engine, select
from fastapi.responses import RedirectResponse, StreamingResponse, Response

from ..model import Job, engine
from ..processing.tasks import TranscribeTask, ScanInboxTask
from ..processing.utils import run_asr, load_audio, write_result, register_job
from ..paths import DATA_PATH, MD_PATH, INBOX_PATH, FILE_PATH

MODE = config("ASR_MODE")

log=logging.getLogger('uvicorn.test')
log.setLevel(logging.DEBUG)

tags_metadata = [
    {
        "name": "files",
        "description": "The uploaded and (processed) audio files are served here.",
    },
    {
        "name": "Job",
        "description": "Transcription jobs.",
    },
    {
        "name": "ASR",
        "description": "Audio Speech Recognition.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    shutdown_event = Event() # type: ignore
    # Startup: Start background tasks
    log.info("Starting background tasks...")
    t = TranscribeTask(shutdown_event)
    t.start()
    s = ScanInboxTask(shutdown_event)
    s.start()
    yield
    # Shutdown: Signal tasks to stop
    log.info("Shutting down background tasks...")
    shutdown_event.set()
    # Note: Depending on how TranscribeTask/ScanInboxTask handle the shutdown_event,
    # you might need additional logic here to ensure they terminate gracefully (e.g., t.join(), s.join()).
    # However, since they likely check the event periodically, setting the event might be sufficient.

app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan)

# @app.get("/")
# def index():

#     return RedirectResponse("/app")
# Serve javascript frontend
app.mount("/", StaticFiles(directory='webapp',html=True), name="app")

# Serve the uploaded files
app.mount("/files/", StaticFiles(directory=FILE_PATH), name="files")


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
    result = run_asr(audio_file.file, markdown=True)
    
    # For obsidian Plugin put the markdown in the segments
    result["segments"] = [{"text": result["md"]}]
    
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
            extension = name_original.split(".")[1]
            name_new = f"{secrets.token_hex(10)}.{extension}"

            async with aiofiles.open(f"{FILE_PATH}/{name_new}", "wb") as buffer:
                content = await audio_file.read() 
                await buffer.write(content)
            job = register_job(name_new, name_original, keep, translate)
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
    job = register_job(filename)
    return job

##### Serve the results

@app.get(
    "/job/{id}",
    summary="Get the result of an asynchronous processing",
    tags=["Job"]
    )
def get_async(id : str):
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
    with Session(engine) as session:
        statement = select(Job)
        jobs = session.exec(statement).all()
        return jobs
