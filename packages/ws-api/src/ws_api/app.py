import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .engine_client import EngineClient
from .tasks_transcribe import TranscribeTask
from .db.engine import create_db_and_tables, populate_default_replacements, populate_initial_prompts

log = logging.getLogger(__name__)

tags_metadata = [
    {"name": "Job", "description": "Transcription jobs."},
    {"name": "Config", "description": "Replacements and prompts configuration."},
]

transcribe_task = None
_engine_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcribe_task, _engine_client

    log.info("Initializing database...")
    create_db_and_tables()
    populate_default_replacements()
    populate_initial_prompts()

    engine_url = config("ENGINE_URL", default="http://localhost:7001")
    _engine_client = EngineClient(base_url=engine_url)
    log.info(f"Engine client configured: {engine_url}")

    shutdown_event = Event()

    log.info("Starting TranscribeTask...")
    transcribe_task = TranscribeTask(shutdown_event, _engine_client)
    transcribe_task.start()

    yield

    log.info("Shutting down...")
    shutdown_event.set()
    if transcribe_task:
        transcribe_task.join(timeout=10)
    if _engine_client:
        _engine_client.close()
    log.info("Background tasks stopped")


app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan, title="ws-api")

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
app.include_router(router)

try:
    app.mount("/static", StaticFiles(directory='webapp', html=True), name="static")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")


@app.get("/")
async def root():
    """Redirect root to static index."""
    return RedirectResponse(url="/static/dictaphone.html")
