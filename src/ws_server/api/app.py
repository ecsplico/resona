import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from core.paths import FILE_PATH
from ..processing.tasks_transcribe import TranscribeTask
from ..processing.task_scan_inbox import ScanInboxTask
from core.db.engine import create_db_and_tables, populate_default_replacements, populate_initial_prompts

log = logging.getLogger(__name__)

tags_metadata = [
    {"name": "files", "description": "The uploaded and (processed) audio files are served here."},
    {"name": "Job", "description": "Transcription jobs."},
    {"name": "ASR", "description": "Audio Speech Recognition."},
    {"name": "Endpoints", "description": "Direct ASR processing."},
]

# Module-level variables for background tasks
transcribe_task = None
scan_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcribe_task, scan_task

    log.info("Initializing database...")
    create_db_and_tables()
    populate_default_replacements()
    populate_initial_prompts()

    shutdown_event = Event()

    log.info("Starting background tasks...")
    transcribe_task = TranscribeTask(shutdown_event)
    transcribe_task.start()
    scan_task = ScanInboxTask(shutdown_event)
    scan_task.start()

    yield

    log.info("Shutting down background tasks...")
    shutdown_event.set()

    if transcribe_task:
        transcribe_task.join(timeout=10)
    if scan_task:
        scan_task.join(timeout=10)
    log.info("Background tasks stopped")


app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan)

# ── CORS ─────────────────────────────────────────────────────────────
CORS_ORIGINS = config("CORS_ORIGINS", default="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health check ─────────────────────────────────────────────────────

@app.get("/health", tags=["Endpoints"])
async def health():
    """Health check endpoint for container orchestration."""
    return {"status": "ok"}


# ── WebSocket endpoints (registered before auth-protected router) ────
from .ws_transcribe import transcribe_websocket
from .ws_live import live_transcribe_websocket


@app.websocket("/ws/transcribe")
async def websocket_transcribe_endpoint(websocket: WebSocket):
    """WebSocket endpoint for file-based transcription."""
    try:
        await transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
        raise


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live transcription with VAD."""
    try:
        await live_transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"Live WebSocket error: {e}", exc_info=True)
        raise


# ── REST API (with auth) ────────────────────────────────────────────
from .endpoints import router
app.include_router(router)

# ── Static files ─────────────────────────────────────────────────────
try:
    app.mount("/static", StaticFiles(directory='webapp', html=True), name="static")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")


@app.get("/")
async def root():
    """Redirect root to static index."""
    return RedirectResponse(url="/static/dictaphone.html")
