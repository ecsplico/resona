"""Worker state machine: submit pending CMS rows as hub jobs, poll, write back.

Mirrors the PWA's in-browser processor (`app/data/localProcessor.ts`): same
transitions, same German error messages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .directus import DirectusClient
from .hub import HubClient, HubError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    collection: str
    file_field: str
    kind: str
    active_status: str


SOURCES = (
    Source("recordings", "audio_file", "audio", "transcribing"),
    Source("documents", "file", "document", "processing"),
)

# Hub statuses that mean "try again later": unreachable/timed out (0), rate
# limited, or a hub restarting behind its proxy. Rows are left untouched.
TRANSIENT_STATUSES = frozenset({0, 429, 502, 503, 504})


class RowError(Exception):
    """A row-level failure with a user-facing (German) message."""


def _error_message(exc: Exception) -> str:
    if isinstance(exc, (HubError, RowError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


async def _submit(
    src: Source, row: dict, directus: DirectusClient, hub: HubClient,
    *, tmp_dir: Path, engine: str,
) -> bool:
    row_id = row["id"]
    file_id = row.get(src.file_field)
    if not file_id:
        await directus.mark_error(src.collection, row_id, "Datei fehlt")
        return True
    if not await directus.claim(src.collection, row_id, src.active_status):
        return False
    path: Path | None = None
    try:
        path = await directus.download_file(file_id, tmp_dir)
        upload_id = await hub.upload(path, row.get("title") or file_id)
        extra = {
            "language": row.get("language") or "de",
            "profile": row.get("profile") or None,
            "engine": engine or None,
        } if src.kind == "audio" else {}
        job_id = await hub.create_job(
            kind=src.kind, upload_id=upload_id,
            idempotency_key=f"{src.collection}:{row_id}:{uuid4().hex}",
            **extra,
        )
        await directus.set_job(src.collection, row_id, job_id)
        log.info("submitted %s %s as hub job %s", src.collection, row_id, job_id)
    finally:
        if path is not None and path.exists():
            path.unlink()
    return True


async def _poll(
    src: Source, row: dict, directus: DirectusClient, hub: HubClient, *, engine: str,
) -> bool:
    row_id = row["id"]
    job_id = int(row["hub_job_id"])
    try:
        job = await hub.get_job(job_id)
        status = job.get("status")
        if status in ("failed", "cancelled"):
            err = job.get("error") or {}
            reason = err.get("message") or err.get("code") or status
            raise RowError(f"Verarbeitung fehlgeschlagen: {reason}")
        if status != "completed":
            return False
        result = await hub.get_result(job_id)
    except HubError as exc:
        if exc.status == 404:
            raise RowError("Hub-Job nicht gefunden") from exc
        if exc.status == 410:
            raise RowError("Ergebnis auf dem Hub abgelaufen") from exc
        raise
    if src.kind == "audio":
        await directus.write_transcript(
            recording_id=row_id,
            text=result.get("markdown") or "",
            language=result.get("language"),
            segments=result.get("segments"),
            structured=result.get("structured"),
            engine=(job.get("candidate") or {}).get("engine") or engine or None,
        )
    else:
        await directus.write_document(
            row_id, markdown=result.get("markdown") or "", pages=result.get("pages"),
        )
    await directus.mark_done(src.collection, row_id)
    log.info("completed %s %s (hub job %s)", src.collection, row_id, job_id)
    try:
        await hub.delete_job(job_id)
    except Exception:  # noqa: BLE001 — the result is already written back
        log.warning("could not delete hub job %s", job_id, exc_info=True)
    return True


async def _guard(src: Source, row: dict, directus: DirectusClient, step) -> bool:
    """Run one row step; isolate its failure. A transiently unavailable hub
    (see `TRANSIENT_STATUSES`) leaves the row as is for the next cycle."""
    try:
        return await step
    except HubError as exc:
        if exc.status in TRANSIENT_STATUSES:
            log.warning(
                "hub unavailable for %s %s, retrying next cycle: %s",
                src.collection, row["id"], exc,
            )
            return False
        exc_to_mark: Exception = exc
    except Exception as exc:  # noqa: BLE001 — worker must never crash on one row
        exc_to_mark = exc
    log.error("%s %s failed: %s", src.collection, row["id"], _error_message(exc_to_mark))
    try:
        await directus.mark_error(src.collection, row["id"], _error_message(exc_to_mark))
    except Exception:
        log.exception("could not mark %s %s as error", src.collection, row["id"])
    return True


async def run_once(
    directus: DirectusClient, hub: HubClient, *,
    tmp_dir: Path, batch: int = 5, stale_minutes: int = 15, engine: str = "",
) -> int:
    """One cycle over all sources. Returns the number of rows that changed state."""
    changed = 0
    for src in SOURCES:
        changed += await directus.reclaim_stale(
            src.collection, src.active_status, older_than_minutes=stale_minutes,
        )
        pending = await directus.list_by_status(src.collection, "pending", limit=batch)
        for row in pending:
            if await _guard(src, row, directus, _submit(
                src, row, directus, hub, tmp_dir=tmp_dir, engine=engine,
            )):
                changed += 1
        active = await directus.list_by_status(
            src.collection, src.active_status, limit=100, with_job=True,
        )
        for row in active:
            if await _guard(src, row, directus, _poll(src, row, directus, hub, engine=engine)):
                changed += 1
    return changed
