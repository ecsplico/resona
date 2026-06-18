"""Async httpx wrapper around the Directus REST API used by the worker."""
from __future__ import annotations

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
                "sort": "date_created",
            },
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def claim(self, recording_id: str) -> bool:
        """Atomically mark a recording as transcribing. Returns True on success."""
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "transcribing"},
        )
        resp.raise_for_status()
        return resp.json()["data"]["status"] == "transcribing"
