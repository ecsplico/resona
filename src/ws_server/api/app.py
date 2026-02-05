import logging
from threading import Event
from contextlib import asynccontextmanager

from decouple import config
from fastapi import FastAPI, WebSocket
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

# CRITICAL: Register WebSocket endpoint FIRST to avoid auth conflicts
from .ws_transcribe import transcribe_websocket

log.info("=" * 60)
log.info("REGISTERING TEST WEBSOCKET: /ws/test")
log.info("=" * 60)

@app.websocket("/ws/test")
async def test_websocket(websocket: WebSocket):
    """Minimal test WebSocket - absolutely no dependencies."""
    log.info("🧪 TEST WebSocket called!")
    await websocket.accept()
    await websocket.send_text("Hello from test WebSocket!")
    await websocket.close()
    log.info("🧪 TEST WebSocket completed")

log.info("✅ TEST WebSocket registered")

log.info("=" * 60)
log.info("REGISTERING WEBSOCKET ROUTE FIRST: /ws/transcribe")
log.info("=" * 60)

@app.websocket("/ws/transcribe")
async def websocket_transcribe_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live transcription - NO AUTH REQUIRED."""
    log.info("🎤 WebSocket endpoint CALLED - connection attempt detected")
    log.info(f"WebSocket client: {websocket.client}")
    try:
        await transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"❌ WebSocket error: {e}", exc_info=True)
        raise

log.info("✅ WebSocket route registered FIRST")

# NOW register the API endpoints AFTER WebSocket
from .endpoints import router
log.info("Registering API router (with auth)...")
app.include_router(router)

log.info("✅ WebSocket route registered successfully")
log.info("=" * 60)

# DEBUG: List all registered routes
log.info("📋 Listing ALL registered routes:")
for route in app.routes:
    log.info(f"  - {route.path} ({type(route).__name__})")
log.info("=" * 60)

# Serve javascript frontend (assuming 'webapp' is in the root directory)
# Mount webapp on /static to avoid interfering with API/WebSocket routes
# IMPORTANT: This must come AFTER all API and WebSocket routes
try:
    app.mount("/static", StaticFiles(directory='webapp', html=True), name="static")
    log.info("Mounted static files at /static")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")

# Add a root redirect to the main app
from fastapi.responses import RedirectResponse

@app.get("/")
async def root():
    """Redirect root to static index or dictaphone."""
    return RedirectResponse(url="/static/dictaphone.html")

# SECURITY NOTE: Removed public /files/ mount
# Files should be accessed through authenticated endpoints only
# If you need to serve files, create a protected endpoint with auth

