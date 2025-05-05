import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Import background tasks and paths from their new locations
from ..processing.tasks import TranscribeTask, ScanInboxTask
from ..core.paths import FILE_PATH # Needed for static file mount

# Basic logging setup (can be enhanced)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('uvicorn.test') # Assuming uvicorn logger is used
log.setLevel(logging.DEBUG) # Or use level from config

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
        "name": "Endpoints", # Keep this if endpoints.py uses it
        "description": "Direct ASR processing.",
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
    # Note: Consider adding t.join() and s.join() here if needed for graceful shutdown

# Initialize FastAPI app
app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan)

# Serve javascript frontend (assuming 'webapp' is in the root directory)
# If 'webapp' is elsewhere, adjust the path accordingly.
app.mount("/", StaticFiles(directory='webapp', html=True), name="app")

# Serve the uploaded files
app.mount("/files/", StaticFiles(directory=FILE_PATH), name="files")

# Import and register the API endpoints
from . import endpoints
