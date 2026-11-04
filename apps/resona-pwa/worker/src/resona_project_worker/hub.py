"""Async client for the Resona hub's durable upload + job API."""
from __future__ import annotations

from pathlib import Path

import httpx


class HubError(Exception):
    """A hub failure carrying a user-facing (German) message.

    `status` is the HTTP status, or 0 when the hub was unreachable / timed out
    (the worker leaves rows untouched in that case and retries next cycle).
    `code` is the hub's `error` code, else `http_<status>` (or `unreachable` /
    `timeout` for status 0).
    """

    def __init__(self, message: str, *, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _detail_from_response(resp: httpx.Response) -> str:
    """Best-effort human-readable detail from an error response body."""
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        for key in ("message", "detail", "error"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    text = (resp.text or "").strip()
    return text[:200]


def _code_from_response(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"http_{resp.status_code}"


class HubClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.ConnectError as exc:
            raise HubError(
                f"Resona-Hub nicht erreichbar ({self.base_url}). Läuft der Hub?",
                status=0, code="unreachable",
            ) from exc
        except httpx.TimeoutException as exc:
            raise HubError(
                f"Zeitüberschreitung beim Resona-Hub ({self.base_url}).",
                status=0, code="timeout",
            ) from exc
        if resp.status_code >= 400:
            detail = _detail_from_response(resp)
            msg = f"Hub-Anfrage fehlgeschlagen (HTTP {resp.status_code})"
            raise HubError(
                f"{msg}: {detail}" if detail else msg,
                status=resp.status_code, code=_code_from_response(resp),
            )
        return resp

    async def upload(self, path: Path, filename: str) -> str:
        with open(path, "rb") as f:
            resp = await self._request("POST", "/v1/uploads", files={"file": (filename, f)})
        return resp.json()["upload_id"]

    async def create_job(
        self, *, kind: str, upload_id: str, idempotency_key: str,
        language: str | None = None, profile: str | None = None, engine: str | None = None,
    ) -> int:
        body: dict = {"kind": kind, "upload_id": upload_id, "private": True}
        for key, val in (("language", language), ("profile", profile), ("engine", engine)):
            if val is not None:
                body[key] = val
        resp = await self._request(
            "POST", "/v1/jobs", json=body, headers={"Idempotency-Key": idempotency_key},
        )
        return int(resp.json()["job_id"])

    async def get_job(self, job_id: int) -> dict:
        return (await self._request("GET", f"/v1/jobs/{job_id}")).json()

    async def get_result(self, job_id: int) -> dict:
        return (await self._request("GET", f"/v1/jobs/{job_id}/result")).json()

    async def delete_job(self, job_id: int) -> None:
        await self._request("DELETE", f"/v1/jobs/{job_id}")
