import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .tasks_transcribe import TranscribeTask
from .db.engine import create_db_and_tables

log = logging.getLogger(__name__)

tags_metadata = [
    {"name": "Job", "description": "Transcription jobs."},
    {"name": "Config", "description": "Postprocessing profiles configuration."},
    {"name": "Audio", "description": "OpenAI-compatible speech API."},
    {"name": "Engines", "description": "Engine discovery."},
]

transcribe_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcribe_task

    log.info("Initializing database...")
    create_db_and_tables()
    from .db.engine import engine as _db_engine
    from .migration import migrate_config_tables_to_profile
    from .paths import PROFILES_PATH
    migrate_config_tables_to_profile(_db_engine, PROFILES_PATH)

    shutdown_event = Event()

    log.info("Starting TranscribeTask...")
    transcribe_task = TranscribeTask(shutdown_event)
    transcribe_task.start()

    yield

    log.info("Shutting down...")
    shutdown_event.set()
    if transcribe_task:
        transcribe_task.join(timeout=10)
    log.info("Background tasks stopped")


app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan, title="resona-api")

CORS_ORIGINS = config("CORS_ORIGINS", default="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Job"])
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


from .endpoints import router
from .audio_routes import router as audio_router
from .profiles_routes import router as profiles_router
app.include_router(router)
app.include_router(audio_router)
app.include_router(profiles_router)

try:
    app.mount("/static", StaticFiles(directory='webapp', html=True), name="static")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")


@app.get("/")
async def root():
    """Redirect root to static index."""
    return RedirectResponse(url="/static/dictaphone.html")
