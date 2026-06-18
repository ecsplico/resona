"""Worker orchestration: poll loop + per-recording processing."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .client import DirectusClient
from .transcribe import TranscribeClient

log = logging.getLogger(__name__)


async def process_one(
    recording: dict, directus: DirectusClient, transcribe: TranscribeClient,
    *, tmp_dir: Path,
) -> None:
    """Download -> transcribe -> write back. Marks error on any failure."""
    rec_id = recording["id"]
    audio_path: Path | None = None
    try:
        audio_path = await directus.download_audio(recording["audio_file"], dest_dir=tmp_dir)
        result = await transcribe.transcribe(
            audio_path,
            language=recording.get("language") or "de",
            profile=recording.get("profile") or "default",
        )
        await directus.write_transcript(
            recording_id=rec_id,
            text=result["text"],
            language=result["language"],
            segments=result["segments"],
            structured=result["structured"],
            engine=result["engine"],
        )
        await directus.mark_done(rec_id)
        log.info("transcribed recording %s", rec_id)
    except Exception as exc:  # noqa: BLE001 — worker must never crash on one job
        log.exception("failed to transcribe recording %s", rec_id)
        try:
            await directus.mark_error(rec_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            log.exception("could not mark recording %s as error", rec_id)
    finally:
        if audio_path is not None and audio_path.exists():
            audio_path.unlink()


async def run_once(
    directus: DirectusClient, transcribe: TranscribeClient, *,
    tmp_dir: Path, concurrency: int = 2, stale_minutes: int = 15,
) -> int:
    """One poll cycle: reclaim stale, claim pending, process with a concurrency cap."""
    await directus.reclaim_stale(older_than_minutes=stale_minutes)
    pending = await directus.list_pending(limit=concurrency * 5)

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(rec: dict) -> bool:
        async with sem:
            if not await directus.claim(rec["id"]):
                return False
            await process_one(rec, directus, transcribe, tmp_dir=tmp_dir)
            return True

    results = await asyncio.gather(*(_guarded(r) for r in pending))
    return sum(1 for r in results if r)
