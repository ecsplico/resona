"""Async client for the Resona hub's durable upload + job API."""
from __future__ import annotations

from pathlib import Path

import httpx


class HubError(Exception):
    """A hub failure carrying a user-facing (German) message.

    `status` is the HTTP status, or 0 when the hub was unreachable / timed out
    (any `httpx.TransportError`; the worker leaves rows untouched on 0 and on
    other transient statuses and retries next cycle). `code` is the hub's
    `error` code, else `http_<status>` (or `unreachable` / `timeout` for status 0).
    `detail` is the hub's own message from the error body, if any.
    """

    def __init__(self, message: str, *, status: int, code: str, detail: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail


def _error_fields(resp: httpx.Response) -> tuple[str | None, str]:
    """Best-effort `(detail, code)` from an error response: a human-readable
    detail (None when the body has none) and the hub's `error` code, else
    `http_<status>`."""
    try:
        body = resp.json()
    except Exception:
        body = None
    code = f"http_{resp.status_code}"
    if isinstance(body, dict):
        if isinstance(body.get("error"), str):
            code = body["error"]
        for key in ("message", "detail", "error"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), code
    text = (resp.text or "").strip()[:200]
    return text or None, code


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
        except httpx.TimeoutException as exc:
            raise HubError(
                f"Zeitüberschreitung beim Resona-Hub ({self.base_url}).",
                status=0, code="timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise HubError(
                f"Resona-Hub nicht erreichbar ({self.base_url}). Läuft der Hub?",
                status=0, code="unreachable",
            ) from exc
        if resp.status_code >= 400:
            detail, code = _error_fields(resp)
            msg = f"Hub-Anfrage fehlgeschlagen (HTTP {resp.status_code})"
            raise HubError(
                f"{msg}: {detail}" if detail else msg,
                status=resp.status_code, code=code, detail=detail,
            )
        return resp

    async def upload(self, path: Path, filename: str) -> str:
        with open(path, "rb") as f:
            resp = await self._request("POST", "/v1/uploads", files={"file": (filename, f)})
        return resp.json()["upload_id"]

    async def upload_text(self, text: str, filename: str) -> str:
        """Uploads `text` as UTF-8; the hub sniffs it as a `text` upload."""
        resp = await self._request(
            "POST", "/v1/uploads",
            files={"file": (filename, text.encode("utf-8"), "text/markdown")},
        )
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
