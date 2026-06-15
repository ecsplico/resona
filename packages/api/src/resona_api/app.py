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


def _validate_env() -> None:
    """Fail fast on misconfiguration; log resolved config summary."""
    from .paths import DATA_PATH, DB_PATH, FILE_PATH, PROFILES_PATH
    from .engine_registry import _engine_urls

    urls = _engine_urls()
    if not urls:
        raise RuntimeError("RESONA_ENGINE_URLS is empty — set at least one engine-server URL")

    for label, path in (("DATA_PATH", DATA_PATH), ("DB_PATH", DB_PATH),
                        ("FILE_PATH", FILE_PATH), ("PROFILES_PATH", PROFILES_PATH)):
        if not path.is_dir():
            raise RuntimeError(f"{label}={path} is not a directory")
        probe = path / ".write_probe"
        try:
            probe.touch(); probe.unlink()
        except OSError as exc:
            raise RuntimeError(f"{label}={path} is not writable: {exc}") from exc

    api_key_set = bool(config("RESONA_API_KEY", default=""))
    if not api_key_set:
        log.warning("RESONA_API_KEY is unset — clients can hit the API without auth")

    log.info("Resolved config:")
    log.info("  DATA_PATH          = %s", DATA_PATH)
    log.info("  RESONA_PROFILES_DIR= %s", PROFILES_PATH)
    log.info("  RESONA_ENGINE_URLS = %s", ",".join(urls))
    log.info("  RESONA_API_KEY     = %s", "set" if api_key_set else "unset")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global transcribe_task

    _validate_env()

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
from .streaming_routes import router as streaming_router
app.include_router(router)
app.include_router(audio_router)
app.include_router(profiles_router)
app.include_router(streaming_router)

try:
    app.mount("/static", StaticFiles(directory='webapp', html=True), name="static")
except RuntimeError:
    log.warning("webapp directory not found, skipping UI mount")


@app.get("/")
async def root():
    """Redirect root to static index."""
    return RedirectResponse(url="/static/dictaphone.html")
