"""Worker state machine: submit pending CMS rows as hub jobs, poll, write back.

Sources: `recordings` (audio → a `transcripts` row), `documents` (document →
`markdown`) and `recording_parts` (re-dictated audio → the part's own `text`; the PWA
lands it in the transcript). Plus transcript re-processing (text jobs) and the
cleanup of part audio the PWA already appended to its recording.

Mirrors the PWA's in-browser processor (`app/data/localProcessor.ts`): same
transitions, same German error messages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .directus import POSTPROCESS_JOB, ROW_JOB, DirectusClient, JobFields
from .hub import HubClient, HubError
from .title import derive_title

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    collection: str
    file_field: str
    kind: str
    active_status: str
    # where a completed job's result goes: "transcript" (a `transcripts` row of the
    # recording), "document" (the row's `markdown`) or "part" (the row's `text`)
    target: str


SOURCES = (
    Source("recordings", "audio_file", "audio", "transcribing", "transcript"),
    Source("documents", "file", "document", "processing", "document"),
    Source("recording_parts", "audio", "audio", "transcribing", "part"),
)

# Re-dictated parts whose audio may be deleted: the result is in (or given up) and
# `offset_seconds` says the PWA appended the audio to the recording's own file.
PARTS_COLLECTION = "recording_parts"
PART_CLEANUP_STATUSES = ("done", "applied", "discarded")

# Hub statuses that mean "try again later": unreachable/timed out (0), rate
# limited, or a hub restarting behind its proxy. Rows are left untouched.
TRANSIENT_STATUSES = frozenset({0, 429, 502, 503, 504})

# Re-processing a stored transcript (`transcripts.postprocess_*`) as a hub text job.
POSTPROCESS_COLLECTION = "transcripts"
POSTPROCESS_ACTIVE = "processing"

# German messages for hub error codes of a text job (same as `app/utils/postprocess.ts`).
POSTPROCESS_ERRORS = {
    "profile_required": "Kein Nachbearbeitungsprofil gewählt.",
    "profile_not_found": "Profil auf dem Hub nicht gefunden.",
    "invalid_profile": "Das Profil auf dem Hub ist ungültig.",
    "policy_denied": "Von der Richtlinie der Organisation abgelehnt.",
}


class RowError(Exception):
    """A row-level failure with a user-facing (German) message."""


def _error_message(exc: Exception) -> str:
    if isinstance(exc, (HubError, RowError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def postprocess_error_message(code: str | None, detail: str | None) -> str | None:
    """German message for a text-job error code; None for codes without one.
    `policy_denied` carries the hub's reason (which stage, which endpoint)."""
    message = POSTPROCESS_ERRORS.get(code or "")
    if message is None:
        return None
    if code == "policy_denied" and detail:
        return f"{message[:-1]}: {detail}"
    return message


async def _submit(
    src: Source, row: dict, directus: DirectusClient, hub: HubClient,
    *, tmp_dir: Path, engine: str,
) -> bool:
    row_id = row["id"]
    file_id = row.get(src.file_field)
    if not file_id:
        await directus.mark_error(src.collection, row_id, "Datei fehlt")
        return True
    # Persisted with the claim, before create_job: if the hub accepts the job but the
    # response is lost, stale recovery keeps the key and the resubmission gets the
    # original job back instead of a second one.
    key = row.get("hub_job_key") or f"{src.collection}:{row_id}:{uuid4().hex}"
    if not await directus.claim(src.collection, row_id, src.active_status, key):
        return False
    path: Path | None = None
    try:
        path = await directus.download_file(file_id, tmp_dir)
        upload_id = await hub.upload(path, _upload_name(src, row, file_id))
        extra = {
            "language": row.get("language") or "de",
            "profile": row.get("profile") or None,
            "engine": engine or None,
        } if src.kind == "audio" else {}
        job_id = await hub.create_job(
            kind=src.kind, upload_id=upload_id,
            idempotency_key=key,
            **extra,
        )
        await directus.set_job(src.collection, row_id, job_id)
        log.info("submitted %s %s as hub job %s", src.collection, row_id, job_id)
    finally:
        if path is not None and path.exists():
            path.unlink()
    return True


def _upload_name(src: Source, row: dict, file_id: str) -> str:
    """Name of the hub upload: the row's title, `part-<id>` for a part (no title)."""
    if src.target == "part":
        return f"part-{row['id']}"
    return row.get("title") or file_id


async def _read_job(hub: HubClient, job_id: int, *, text_job: bool = False) -> tuple[dict, dict | None]:
    """The hub job and, once it completed, its result. Raises RowError when the
    job failed or is gone; the result is None while the job still runs."""
    try:
        job = await hub.get_job(job_id)
        status = job.get("status")
        if status in ("failed", "cancelled"):
            err = job.get("error") or {}
            if text_job and err.get("code") == "policy_denied":
                raise RowError(postprocess_error_message("policy_denied", err.get("message")))
            reason = err.get("message") or err.get("code") or status
            raise RowError(f"Verarbeitung fehlgeschlagen: {reason}")
        if status != "completed":
            return job, None
        return job, await hub.get_result(job_id)
    except HubError as exc:
        if exc.status == 404:
            raise RowError("Hub-Job nicht gefunden") from exc
        if exc.status == 410:
            raise RowError("Ergebnis auf dem Hub abgelaufen") from exc
        raise


async def _delete_job(hub: HubClient, job_id: int) -> None:
    try:
        await hub.delete_job(job_id)
    except Exception:  # noqa: BLE001 — the result is already written back
        log.warning("could not delete hub job %s", job_id, exc_info=True)


async def _poll(
    src: Source, row: dict, directus: DirectusClient, hub: HubClient, *, engine: str,
) -> bool:
    row_id = row["id"]
    job_id = int(row["hub_job_id"])
    job, result = await _read_job(hub, job_id)
    if result is None:
        return False
    if src.target == "part":
        # Text only: the PWA places it in the transcript (anchor, conflict handling),
        # so no transcript row and no title here. One patch sets status `done` too.
        if await directus.write_part(row_id, text=result.get("markdown") or ""):
            log.info("completed %s %s (hub job %s)", src.collection, row_id, job_id)
        else:
            log.info("%s %s left its job meanwhile (hub job %s)", src.collection, row_id, job_id)
        await _delete_job(hub, job_id)
        return True
    if src.target == "transcript":
        superseded = await directus.write_transcript(
            recording_id=row_id,
            text=result.get("markdown") or "",
            language=result.get("language"),
            segments=result.get("segments"),
            structured=result.get("structured"),
            provenance=result.get("provenance"),
            engine=(job.get("candidate") or {}).get("engine") or engine or None,
        )
        # A text job still running on the previous transcript would write stale output.
        if superseded is not None:
            await _delete_job(hub, int(superseded))
        if row.get("title_auto"):
            # Best effort: the transcript is written, so a failed title never fails the row.
            try:
                await _apply_derived_title(src, row_id, result.get("markdown") or "", directus)
            except Exception as exc:  # noqa: BLE001
                # Type only: the error could carry the derived title (transcript text).
                log.warning(
                    "could not apply derived title to %s %s: %s",
                    src.collection, row_id, type(exc).__name__,
                )
    else:
        await directus.write_document(
            row_id, markdown=result.get("markdown") or "", pages=result.get("pages"),
            provenance=result.get("provenance"),
        )
    await directus.mark_done(src.collection, row_id)
    log.info("completed %s %s (hub job %s)", src.collection, row_id, job_id)
    await _delete_job(hub, job_id)
    return True


async def _apply_derived_title(
    src: Source, row_id: str, markdown: str, directus: DirectusClient,
) -> None:
    """Replaces a placeholder title with one derived from the transcript. The update
    is filtered on `title_auto` (`set_auto_title`), so a rename while the job ran wins."""
    title = derive_title(markdown)
    if title:
        await directus.set_auto_title(src.collection, row_id, title)


def _postprocess_input(row: dict) -> str:
    """The raw transcript a profile runs on: `raw_text`, else (never re-processed) `text`."""
    raw = row.get("raw_text")
    return raw if raw is not None else (row.get("text") or "")


async def _submit_text(row: dict, directus: DirectusClient, hub: HubClient) -> bool:
    row_id = row["id"]
    profile = row.get("postprocess_profile")
    source = _postprocess_input(row)
    if not profile:
        raise RowError(POSTPROCESS_ERRORS["profile_required"])
    if not source.strip():
        raise RowError("Kein Transkript-Text zum Nachbearbeiten.")
    # A pending row that still carries a job was re-requested while that job ran
    # (users cannot clear the job fields): the old run is superseded, so it is
    # deleted on the hub and the new one gets a fresh key (the old key would
    # return the old job).
    superseded = row.get("postprocess_job_id")
    key = (None if superseded is not None else row.get("postprocess_job_key")) \
        or f"{POSTPROCESS_COLLECTION}:{row_id}:{uuid4().hex}"
    if not await directus.claim(
        POSTPROCESS_COLLECTION, row_id, POSTPROCESS_ACTIVE, key, fields=POSTPROCESS_JOB,
    ):
        return False
    if superseded is not None:
        await _delete_job(hub, int(superseded))
    try:
        upload_id = await hub.upload_text(source, "transcript.md")
        job_id = await hub.create_job(
            kind="text", upload_id=upload_id, idempotency_key=key, profile=profile,
        )
    except HubError as exc:
        mapped = postprocess_error_message(exc.code, exc.detail)
        if mapped is None or exc.status in TRANSIENT_STATUSES:
            raise
        raise RowError(mapped) from exc
    await directus.set_job(POSTPROCESS_COLLECTION, row_id, job_id, fields=POSTPROCESS_JOB)
    log.info("submitted transcript %s for profile %s as hub job %s", row_id, profile, job_id)
    return True


async def _poll_text(row: dict, directus: DirectusClient, hub: HubClient) -> bool:
    row_id = row["id"]
    job_id = int(row["postprocess_job_id"])
    job, result = await _read_job(hub, job_id, text_job=True)
    if result is None:
        # Waiting: tell the user why (e.g. `no_capable_worker` while the worker that
        # serves the profile's endpoint is offline). Written only when it changes.
        reason = job.get("queue_reason")
        if reason != row.get("postprocess_queue_reason"):
            await directus.patch(
                POSTPROCESS_COLLECTION, row_id, {"postprocess_queue_reason": reason},
            )
        return False
    await directus.write_postprocessed(
        row_id,
        text=result.get("markdown") or "",
        raw_text=_postprocess_input(row),
        profile=row.get("postprocess_profile") or "",
    )
    log.info("re-processed transcript %s (hub job %s)", row_id, job_id)
    await _delete_job(hub, job_id)
    return True


async def _guard(
    collection: str, row: dict, directus: DirectusClient, step,
    *, fields: JobFields = ROW_JOB,
) -> bool:
    """Run one row step; isolate its failure. A transiently unavailable hub
    (see `TRANSIENT_STATUSES`) leaves the row as is for the next cycle."""
    try:
        return await step
    except HubError as exc:
        if exc.status in TRANSIENT_STATUSES:
            log.warning(
                "hub unavailable for %s %s, retrying next cycle: %s",
                collection, row["id"], exc,
            )
            return False
        exc_to_mark: Exception = exc
    except Exception as exc:  # noqa: BLE001 — worker must never crash on one row
        exc_to_mark = exc
    log.error("%s %s failed: %s", collection, row["id"], _error_message(exc_to_mark))
    try:
        await directus.mark_error(
            collection, row["id"], _error_message(exc_to_mark), fields=fields,
        )
    except Exception:
        log.exception("could not mark %s %s as error", collection, row["id"])
    return True


async def _run_postprocess(
    directus: DirectusClient, hub: HubClient, *, batch: int, stale_minutes: int,
) -> int:
    changed = await directus.reclaim_stale(
        POSTPROCESS_COLLECTION, POSTPROCESS_ACTIVE, older_than_minutes=stale_minutes,
        fields=POSTPROCESS_JOB,
    )
    pending = await directus.list_by_status(
        POSTPROCESS_COLLECTION, "pending", limit=batch, fields=POSTPROCESS_JOB,
    )
    for row in pending:
        if await _guard(
            POSTPROCESS_COLLECTION, row, directus, _submit_text(row, directus, hub),
            fields=POSTPROCESS_JOB,
        ):
            changed += 1
    active = await directus.list_by_status(
        POSTPROCESS_COLLECTION, POSTPROCESS_ACTIVE, limit=100, with_job=True,
        fields=POSTPROCESS_JOB,
    )
    for row in active:
        if await _guard(
            POSTPROCESS_COLLECTION, row, directus, _poll_text(row, directus, hub),
            fields=POSTPROCESS_JOB,
        ):
            changed += 1
    return changed


async def _cleanup_parts(directus: DirectusClient, *, batch: int) -> int:
    """Deletes the audio of parts the PWA already appended to their recording.

    Only parts with `offset_seconds` set: before that the page still has to append
    the audio. A file that is already gone (404) counts as deleted. Failures are
    logged and retried next cycle; they never stop the loop. Returns parts cleaned.
    """
    try:
        rows = await directus.list_part_audio_to_clean(PART_CLEANUP_STATUSES, limit=batch)
    except Exception:  # noqa: BLE001 — cleanup must never crash the worker
        log.exception("could not list recording parts for audio cleanup")
        return 0
    cleaned = 0
    for row in rows:
        try:
            await directus.delete_file(row["audio"])
            await directus.patch(PARTS_COLLECTION, row["id"], {"audio": None})
        except Exception:  # noqa: BLE001
            log.exception("could not clean up audio of recording part %s", row["id"])
            continue
        cleaned += 1
        log.info("deleted audio of recording part %s", row["id"])
    return cleaned


async def run_once(
    directus: DirectusClient, hub: HubClient, *,
    tmp_dir: Path, batch: int = 5, stale_minutes: int = 15, engine: str = "",
) -> int:
    """One cycle over all sources, pending transcript re-processing and the cleanup
    of appended part audio. Returns the number of rows that changed state."""
    changed = 0
    for src in SOURCES:
        changed += await directus.reclaim_stale(
            src.collection, src.active_status, older_than_minutes=stale_minutes,
        )
        pending = await directus.list_by_status(src.collection, "pending", limit=batch)
        for row in pending:
            if await _guard(src.collection, row, directus, _submit(
                src, row, directus, hub, tmp_dir=tmp_dir, engine=engine,
            )):
                changed += 1
        active = await directus.list_by_status(
            src.collection, src.active_status, limit=100, with_job=True,
        )
        for row in active:
            if await _guard(
                src.collection, row, directus, _poll(src, row, directus, hub, engine=engine),
            ):
                changed += 1
    changed += await _run_postprocess(
        directus, hub, batch=batch, stale_minutes=stale_minutes,
    )
    changed += await _cleanup_parts(directus, batch=batch)
    return changed
