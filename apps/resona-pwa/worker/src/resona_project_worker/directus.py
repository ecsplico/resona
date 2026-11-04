"""Async httpx wrapper around the Directus REST API used by the worker."""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx


class DirectusClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _patch(self, collection: str, item_id: str, payload: dict) -> dict:
        resp = await self._client.patch(
            f"{self.base_url}/items/{collection}/{item_id}", json=payload,
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def list_by_status(
        self, collection: str, status: str, *, limit: int, with_job: bool | None = None,
    ) -> list[dict]:
        """Rows in `status`, oldest first. `with_job` filters on `hub_job_id`
        being set (True) or empty (False); None does not filter."""
        params: dict = {
            "filter[status][_eq]": status,
            "limit": limit,
            "sort": "date_created",  # ascending = FIFO (oldest first)
        }
        if with_job is True:
            params["filter[hub_job_id][_nnull]"] = "true"
        elif with_job is False:
            params["filter[hub_job_id][_null]"] = "true"
        resp = await self._client.get(f"{self.base_url}/items/{collection}", params=params)
        resp.raise_for_status()
        return resp.json()["data"]

    async def claim(
        self, collection: str, item_id: str, active_status: str, job_key: str,
    ) -> bool:
        """Optimistically mark a row as `active_status`. Returns True on success.

        Also clears `hub_job_id`, so a retried row never polls its previous job,
        and persists `job_key` (the hub Idempotency-Key) before the job is created,
        so a resubmission after a lost create response reuses it.

        This is an unconditional PATCH, not a compare-and-swap — Directus has no
        native CAS. Two workers racing on the same page could both "claim" the
        same row. The stale-claim recovery (reclaim_stale) plus a single-worker
        deployment are the agreed mitigations.
        """
        data = await self._patch(
            collection, item_id,
            {"status": active_status, "hub_job_id": None, "hub_job_key": job_key},
        )
        return data["status"] == active_status

    async def set_job(self, collection: str, item_id: str, job_id: int) -> None:
        await self._patch(collection, item_id, {"hub_job_id": job_id, "error_message": None})

    async def reclaim_stale(
        self, collection: str, active_status: str, older_than_minutes: int = 15,
        limit: int = 100,
    ) -> int:
        """Reset rows stuck in `active_status` without a hub job back to `pending`.

        Rows with `hub_job_id` set are owned by a durable hub job and are never
        reclaimed (that would submit a second job). Returns count reset.
        """
        resp = await self._client.get(
            f"{self.base_url}/items/{collection}",
            params={
                "filter[status][_eq]": active_status,
                "filter[hub_job_id][_null]": "true",
                "filter[date_updated][_lt]": f"$NOW(-{older_than_minutes} minutes)",
                "limit": limit,
            },
        )
        resp.raise_for_status()
        stale = resp.json()["data"]
        for row in stale:
            await self._patch(collection, row["id"], {"status": "pending"})
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
    ) -> None:
        # `language` lives on the recording, not the transcript collection —
        # accepted for a uniform call site but intentionally not POSTed.
        payload = {
            "text": text,
            "segments": segments,
            "structured": structured,
            "engine": engine,
        }
        existing = await self._client.get(
            f"{self.base_url}/items/transcripts",
            params={"filter[recording][_eq]": recording_id, "limit": 1, "fields": "id"},
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

    async def write_document(self, document_id: str, *, markdown: str, pages: list | None) -> None:
        await self._patch("documents", document_id, {"markdown": markdown, "pages": pages})

    async def mark_done(self, collection: str, item_id: str) -> None:
        await self._patch(
            collection, item_id, {"status": "done", "hub_job_id": None, "hub_job_key": None},
        )

    async def mark_error(self, collection: str, item_id: str, message: str) -> None:
        await self._patch(
            collection, item_id,
            {
                "status": "error", "error_message": message[:1000],
                "hub_job_id": None, "hub_job_key": None,
            },
        )
