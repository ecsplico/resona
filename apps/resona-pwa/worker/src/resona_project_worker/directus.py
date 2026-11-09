"""Async httpx wrapper around the Directus REST API used by the worker."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class JobFields:
    """Names of the fields that carry one hub job's state on a row."""

    status: str
    job_id: str
    job_key: str
    error: str
    # Why a queued job waits (hub `queue_reason`); None when the row keeps none.
    queue_reason: str | None = None

    def cleared(self) -> dict:
        """Payload that clears a run's job, error and queue reason."""
        payload = {self.job_id: None, self.job_key: None, self.error: None}
        if self.queue_reason:
            payload[self.queue_reason] = None
        return payload


# recordings / documents: the row's own transcription or conversion job
ROW_JOB = JobFields("status", "hub_job_id", "hub_job_key", "error_message")
# transcripts: re-processing the stored raw transcript with a postprocess profile
POSTPROCESS_JOB = JobFields(
    "postprocess_status", "postprocess_job_id", "postprocess_job_key", "postprocess_error",
    "postprocess_queue_reason",
)


class DirectusClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def patch(self, collection: str, item_id: str, payload: dict) -> dict:
        resp = await self._client.patch(
            f"{self.base_url}/items/{collection}/{item_id}", json=payload,
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def list_by_status(
        self, collection: str, status: str, *, limit: int, with_job: bool | None = None,
        fields: JobFields = ROW_JOB,
    ) -> list[dict]:
        """Rows in `status`, oldest first. `with_job` filters on the job id
        being set (True) or empty (False); None does not filter."""
        params: dict = {
            f"filter[{fields.status}][_eq]": status,
            "limit": limit,
            "sort": "date_created",  # ascending = FIFO (oldest first)
        }
        if with_job is True:
            params[f"filter[{fields.job_id}][_nnull]"] = "true"
        elif with_job is False:
            params[f"filter[{fields.job_id}][_null]"] = "true"
        resp = await self._client.get(f"{self.base_url}/items/{collection}", params=params)
        resp.raise_for_status()
        return resp.json()["data"]

    async def claim(
        self, collection: str, item_id: str, active_status: str, job_key: str,
        *, fields: JobFields = ROW_JOB,
    ) -> bool:
        """Optimistically mark a row as `active_status`. Returns True on success.

        Also clears the job id, error and queue reason, so a retried row never
        polls its previous job or shows its old failure, and persists `job_key` (the hub Idempotency-Key) before the job is created,
        so a resubmission after a lost create response reuses it.

        This is an unconditional PATCH, not a compare-and-swap — Directus has no
        native CAS. Two workers racing on the same page could both "claim" the
        same row. The stale-claim recovery (reclaim_stale) plus a single-worker
        deployment are the agreed mitigations.
        """
        data = await self.patch(
            collection, item_id,
            {**fields.cleared(), fields.status: active_status, fields.job_key: job_key},
        )
        return data[fields.status] == active_status

    async def set_job(
        self, collection: str, item_id: str, job_id: int, *, fields: JobFields = ROW_JOB,
    ) -> None:
        await self.patch(collection, item_id, {fields.job_id: job_id, fields.error: None})

    async def reclaim_stale(
        self, collection: str, active_status: str, older_than_minutes: int = 15,
        limit: int = 100, *, fields: JobFields = ROW_JOB,
    ) -> int:
        """Reset rows stuck in `active_status` without a hub job back to `pending`.

        Rows with a job id set are owned by a durable hub job and are never
        reclaimed (that would submit a second job). Returns count reset.
        """
        resp = await self._client.get(
            f"{self.base_url}/items/{collection}",
            params={
                f"filter[{fields.status}][_eq]": active_status,
                f"filter[{fields.job_id}][_null]": "true",
                "filter[date_updated][_lt]": f"$NOW(-{older_than_minutes} minutes)",
                "limit": limit,
            },
        )
        resp.raise_for_status()
        stale = resp.json()["data"]
        for row in stale:
            await self.patch(collection, row["id"], {fields.status: "pending"})
        return len(stale)

    async def download_file(self, file_id: str, dest_dir: Path) -> Path:
        resp = await self._client.get(f"{self.base_url}/assets/{file_id}")
        resp.raise_for_status()
        dest = dest_dir / f"{file_id}-{uuid.uuid4().hex}.bin"
        dest.write_bytes(resp.content)
        return dest

    async def write_transcript(
        self, *, recording_id: str, text: str, language: str | None,
        segments: list | None, structured: dict | None, engine: str | None,
        provenance: dict | None,
    ) -> int | None:
        """Creates or replaces the recording's transcript. A fresh transcription is
        raw again: an earlier re-processing and its job state are dropped. Returns the
        hub text job the old transcript still had in flight (the caller deletes it)."""
        # `language` lives on the recording, not the transcript collection —
        # accepted for a uniform call site but intentionally not POSTed.
        payload = {
            "text": text,
            "segments": segments,
            "structured": structured,
            "engine": engine,
            "provenance": provenance,
            "raw_text": None,
            "applied_profile": None,
            "processed_text": None,
            "postprocess_status": None,
            "postprocess_profile": None,
            **POSTPROCESS_JOB.cleared(),
        }
        existing = await self._client.get(
            f"{self.base_url}/items/transcripts",
            params={"filter[recording][_eq]": recording_id, "limit": 1, "fields": "id,postprocess_job_id"},
        )
        existing.raise_for_status()
        rows = existing.json()["data"]
        if rows:
            resp = await self._client.patch(
                f"{self.base_url}/items/transcripts/{rows[0]['id']}",
                json=payload,
            )
        else:
            resp = await self._client.post(
                f"{self.base_url}/items/transcripts",
                json={"recording": recording_id, **payload},
            )
        resp.raise_for_status()
        return rows[0].get("postprocess_job_id") if rows else None

    async def write_postprocessed(
        self, transcript_id: str, *, text: str, raw_text: str, profile: str,
    ) -> None:
        """Stores a re-processed transcript: `text` becomes the profile's output,
        `raw_text` keeps the input so the profile can be re-run or undone."""
        await self.patch("transcripts", transcript_id, {
            "text": text,
            "raw_text": raw_text,
            "applied_profile": profile,
            "processed_text": text,
            "postprocess_status": "done",
            **POSTPROCESS_JOB.cleared(),
        })

    async def write_document(
        self, document_id: str, *, markdown: str, pages: list | None, provenance: dict | None,
    ) -> None:
        await self.patch(
            "documents", document_id,
            {"markdown": markdown, "pages": pages, "provenance": provenance},
        )

    async def mark_done(self, collection: str, item_id: str) -> None:
        await self.patch(
            collection, item_id, {"status": "done", "hub_job_id": None, "hub_job_key": None},
        )

    async def mark_error(
        self, collection: str, item_id: str, message: str, *, fields: JobFields = ROW_JOB,
    ) -> None:
        await self.patch(
            collection, item_id,
            {**fields.cleared(), fields.status: "error", fields.error: message[:1000]},
        )
