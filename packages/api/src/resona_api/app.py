import asyncio
import contextlib
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from decouple import config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import hublock
from .db.engine import create_db_and_tables
from .errors import install_error_handlers
from .maintenance import maintenance_loop

log = logging.getLogger(__name__)

tags_metadata = [
    {"name": "Job", "description": "Transcription jobs."},
    {"name": "Config", "description": "Postprocessing profiles configuration."},
    {"name": "Audio", "description": "OpenAI-compatible speech API."},
    {"name": "Engines", "description": "Engine discovery."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


def _validate_env() -> None:
    """Fail fast on misconfiguration; log resolved config summary."""
    from .paths import DATA_PATH, DB_PATH, FILE_PATH
    from .engine_registry import _engine_urls

    urls = _engine_urls()
    if not urls:
        raise RuntimeError("RESONA_ENGINE_URLS is empty — set at least one engine-server URL")

    for label, path in (("DATA_PATH", DATA_PATH), ("DB_PATH", DB_PATH),
                        ("FILE_PATH", FILE_PATH)):
        if not path.is_dir():
            raise RuntimeError(f"{label}={path} is not a directory")
        probe = path / ".write_probe"
        try:
            probe.touch(); probe.unlink()
        except OSError as exc:
            raise RuntimeError(f"{label}={path} is not writable: {exc}") from exc

    log.info("Hosted authentication: per-organization stored credentials (no shared key)")

    log.info("Resolved config:")
    log.info("  DATA_PATH          = %s", DATA_PATH)
    log.info("  RESONA_ENGINE_URLS = %s", ",".join(urls))


# Name pattern of the Markdown/JSON exports the removed in-hub transcriber wrote
# ("<date> <id or patient> (<job id>).md" plus a ".json" sidecar).
_LEGACY_EXPORT = re.compile(r"^\d{4}-\d{2}-\d{2} .+ \(\d+\)\.(md|json)$")


def _remove_legacy_markdown_exports() -> int:
    """Delete legacy result exports under ``MD_PATH`` (default ``<DATA_PATH>/md``).

    Their names can contain original upload or patient names and they fall outside
    the retention policy. Only files matching the export pattern are removed; the
    directory is removed when that leaves it empty. Idempotent.
    """
    from .paths import DATA_PATH

    configured = config("MD_PATH", default="")
    directory = Path(configured) if configured else DATA_PATH / "md"
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.iterdir():
        if entry.is_file() and _LEGACY_EXPORT.fullmatch(entry.name):
            entry.unlink(missing_ok=True)
            removed += 1
    with contextlib.suppress(OSError):
        directory.rmdir()
    return removed


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_env()
    # One hub per data volume; offline restores take the same lock.
    lock = hublock.acquire()
    try:
        log.info("Initializing database...")
        create_db_and_tables()
        removed = _remove_legacy_markdown_exports()
        if removed:
            log.info("Removed %d legacy result export files", removed)

        log.info("Starting maintenance loop...")
        maintenance = asyncio.create_task(maintenance_loop())

        yield

        log.info("Shutting down...")
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
        log.info("Background tasks stopped")
    finally:
        lock.release()


app = FastAPI(openapi_tags=tags_metadata, lifespan=lifespan, title="resona-api")
install_error_handlers(app)

CORS_ORIGINS = config("CORS_ORIGINS", default="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from .health_routes import router as health_router
from .jobs_routes import router as jobs_router
from .worker_routes import router as worker_router
from .audio_routes import router as audio_router
from .profiles_routes import router as profiles_router
from .settings_routes import router as settings_router
from .streaming_routes import router as streaming_router
from .evaluations_routes import router as evaluations_router
from .admin_routes import router as admin_router, static_router as admin_static_router
app.include_router(jobs_router)
app.include_router(health_router)
app.include_router(worker_router)
app.include_router(audio_router)
app.include_router(profiles_router)
app.include_router(settings_router)
app.include_router(streaming_router)
app.include_router(evaluations_router)
app.include_router(admin_router)
app.include_router(admin_static_router)
