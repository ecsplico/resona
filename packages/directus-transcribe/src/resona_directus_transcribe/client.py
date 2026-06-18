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

    async def list_pending(self, limit: int = 10) -> list[dict]:
        resp = await self._client.get(
            f"{self.base_url}/items/recordings",
            params={
                "filter[status][_eq]": "pending",
                "limit": limit,
                "sort": "date_created",  # ascending = FIFO (oldest pending first)
            },
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def claim(self, recording_id: str) -> bool:
        """Optimistically mark a recording as transcribing. Returns True on success.

        This is an unconditional PATCH, not a compare-and-swap — Directus has no
        native CAS. Two workers racing on the same list_pending page could both
        "claim" the same recording. The stale-claim recovery (reclaim_stale) plus
        a single-worker deployment are the agreed mitigations.
        """
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "transcribing"},
        )
        resp.raise_for_status()
        return resp.json()["data"]["status"] == "transcribing"

    async def download_audio(self, file_id: str, dest_dir: "Path") -> "Path":
        # TODO: stream (self._client.stream + aiter_bytes) for large audio files
        # to avoid buffering the whole payload in memory.
        resp = await self._client.get(f"{self.base_url}/assets/{file_id}")
        resp.raise_for_status()
        dest = dest_dir / f"{file_id}-{uuid.uuid4().hex}.audio"
        dest.write_bytes(resp.content)
        return dest

    async def write_transcript(
        self, *, recording_id: str, text: str, language: str | None,
        segments: list | None, structured: dict | None, engine: str | None,
    ) -> None:
        # `language` lives on the recording, not the transcript collection —
        # accepted for a uniform call site but intentionally not POSTed.
        resp = await self._client.post(
            f"{self.base_url}/items/transcripts",
            json={
                "recording": recording_id,
                "text": text,
                "segments": segments,
                "structured": structured,
                "engine": engine,
            },
        )
        resp.raise_for_status()

    async def mark_done(self, recording_id: str) -> None:
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "done"},
        )
        resp.raise_for_status()

    async def mark_error(self, recording_id: str, message: str) -> None:
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "error", "error_message": message[:1000]},
        )
        resp.raise_for_status()
