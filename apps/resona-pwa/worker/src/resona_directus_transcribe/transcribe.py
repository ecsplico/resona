"""Async client for the resona-api OpenAI-compatible transcription route."""
from __future__ import annotations

from pathlib import Path

import httpx


class TranscribeClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 3600.0):
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, audio_path: Path, *, language: str = "de",
        profile: str = "default", engine: str | None = None,
    ) -> dict:
        data = {
            "response_format": "verbose_json",
            "language": language,
            "profile": profile,
        }
        if engine:
            data["engine"] = engine
        with open(audio_path, "rb") as f:
            resp = await self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f, "audio/wav")},
                data=data,
            )
        resp.raise_for_status()
        body = resp.json()
        return {
            "text": body.get("text", ""),
            "language": body.get("language"),
            "segments": body.get("segments"),
            "structured": body.get("structured"),  # best-effort; route omits it today
            "engine": engine,
        }
