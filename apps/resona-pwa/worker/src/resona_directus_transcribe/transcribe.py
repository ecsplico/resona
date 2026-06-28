"""Async client for the resona-api OpenAI-compatible transcription route."""
from __future__ import annotations

from pathlib import Path

import httpx


class TranscribeError(Exception):
    """A transcription failure carrying a user-facing (German) message.

    Raised instead of leaking the raw httpx exception so the message stored on
    the recording — and shown in the UI — reads clearly (e.g. "service not
    reachable") rather than `HTTPStatusError: Client error '400 …'`.
    """


def _detail_from_response(resp: httpx.Response) -> str:
    """Best-effort human-readable detail from an error response body."""
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    text = (resp.text or "").strip()
    return text[:200]


class TranscribeClient:
    def __init__(
        self, base_url: str, api_key: str = "", *,
        engine: str | None = None, timeout: float = 3600.0,
    ):
        self.base_url = base_url.rstrip("/")
        # Engine requested for every job (and stamped on the transcript) unless a
        # per-call engine overrides it. resona-api picks its own default if None.
        self.engine = engine
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, audio_path: Path, *, language: str = "de",
        profile: str = "default", engine: str | None = None,
    ) -> dict:
        eng = engine or self.engine
        data = {
            "response_format": "verbose_json",
            "language": language,
            "profile": profile,
        }
        if eng:
            data["engine"] = eng
        url = f"{self.base_url}/v1/audio/transcriptions"
        try:
            with open(audio_path, "rb") as f:
                resp = await self._client.post(
                    url,
                    files={"file": (audio_path.name, f, "audio/wav")},
                    data=data,
                )
        except httpx.ConnectError as exc:
            # Host unreachable — service not running / wrong URL / network down.
            raise TranscribeError(
                f"Transkriptionsdienst nicht erreichbar ({self.base_url}). "
                "Läuft der Backend-Dienst?",
            ) from exc
        except httpx.TimeoutException as exc:
            raise TranscribeError(
                f"Zeitüberschreitung beim Transkriptionsdienst ({self.base_url}).",
            ) from exc

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _detail_from_response(resp)
            msg = f"Transkription fehlgeschlagen (HTTP {resp.status_code})"
            raise TranscribeError(f"{msg}: {detail}" if detail else msg) from exc

        body = resp.json()
        return {
            "text": body.get("text", ""),
            "language": body.get("language"),
            "segments": body.get("segments"),
            "structured": body.get("structured"),  # best-effort; route omits it today
            "engine": eng,
        }
