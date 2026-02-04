import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Import background tasks and paths from their new locations
from core.paths import FILE_PATH
from ..processing.tasks_transcribe import TranscribeTask
from ..processing.task_scan_inbox import ScanInboxTask

# Import database initialization functions
from core.db.engine import create_db_and_tables, populate_default_replacements, populate_initial_prompts

# Basic logging setup (can be enhanced)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('uvicorn.test')
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
     {
        "name": "Endpoints",
        "description": "Direct ASR processing.",
    },
]

# Module-level variables for background tasks
transcribe_task = None
scan_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcribe_task, scan_task
    
    # Startup: Initialize database
    log.info("Initializing database...")
    create_db_and_tables()
    populate_default_replacements()
    populate_initial_prompts()
    
    shutdown_event = Event()  # type: ignore
    
    # Startup: Start background tasks
    log.info("Starting background tasks...")
    transcribe_task = TranscribeTask(shutdown_event)
    transcribe_task.start()
    scan_task = ScanInboxTask(shutdown_event)
    scan_task.start()
    
    yield
    
    # Shutdown: Signal tasks to stop and wait for completion
    log.info("Shutting down background tasks...")
    shutdown_event.set()
    
    if transcribe_task:
        transcribe_task.join(timeout=10)
        log.info("Transcribe task stopped")
    
    if scan_task:
        scan_task.join(timeout=10)
        log.info("Scan task stopped")


# Initialize FastAPI app
app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan)

# Serve javascript frontend (assuming 'webapp' is in the root directory)
# Mount webapp for UI if it exists
try:
    app.mount("/", StaticFiles(directory='webapp', html=True), name="app")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")

# SECURITY NOTE: Removed public /files/ mount
# Files should be accessed through authenticated endpoints only
# If you need to serve files, create a protected endpoint with auth

# Import and register the API endpoints
from .endpoints import router
app.include_router(router)

