"""
ResonaClient — HTTP client for the Resona API.

Configuration (in priority order):
  1. Explicit base_url / api_key arguments
  2. RESONA_API_URL / RESONA_API_KEY environment variables
  3. WS_API_URL / WS_API_KEY environment variables (fallback for migration)
  4. ~/.resona/config.json (via ResonaClient.from_config())
"""
import os
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class ResonaClient:
    """HTTP client for resona-api and resona-engine."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 3600.0,
    ):
        self.base_url = (
            base_url
            or os.getenv("RESONA_API_URL")
            or os.getenv("WS_API_URL", "http://localhost:7000")
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.getenv("RESONA_API_KEY")
            or os.getenv("WS_API_KEY", "")
        )
        self._client = httpx.Client(
            timeout=timeout,
            headers={"X-API-Key": self.api_key} if self.api_key else {},
        )

    @classmethod
    def from_config(cls, auto_start: bool = True, timeout: float = 3600.0) -> "ResonaClient":
        """
        Create a client by resolving the engine to use:

        1. RESONA_API_URL env var (if set) — used directly, no config lookup.
        2. WS_API_URL env var (if set) — used directly, no config lookup (fallback).
        3. First reachable engine in ~/.resona/config.json.
        4. If none reachable and an engine has compose_dir set, start it via
           docker compose up -d and wait for it to become healthy.

        Raises RuntimeError if no engine could be resolved.
        """
        env_url = os.getenv("RESONA_API_URL") or os.getenv("WS_API_URL")
        if env_url:
            return cls(base_url=env_url, timeout=timeout)

        from .config import resolve_engine
        entry = resolve_engine(auto_start=auto_start)
        if entry:
            return cls(base_url=entry.api_url, api_key=entry.api_key, timeout=timeout)

        raise RuntimeError(
            "No reachable resona engine found.\n"
            "Add one with:  resona engines add <name> <url>"
        )

    # ── Job API operations ────────────────────────────────────────────

    def submit_job(
        self,
        filepath: Path | str,
        keep: bool = True,
        translate: bool = False,
        engine: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> dict:
        """Upload an audio file and register it for async transcription. POST /jobs"""
        filepath = Path(filepath)
        data: dict = {"keep": str(keep).lower(), "translate": str(translate).lower()}
        if engine:
            data["engine"] = engine
        if profile:
            data["profile"] = profile
        with open(filepath, "rb") as f:
            resp = self._client.post(
                f"{self.base_url}/jobs",
                files={"audio_files": (filepath.name, f, "audio/wav")},
                data=data,
            )
        resp.raise_for_status()
        jobs = resp.json()
        return jobs[0] if isinstance(jobs, list) else jobs

    def get_job(self, job_id: int) -> dict:
        """Get job status and result. GET /jobs/{id}"""
        resp = self._client.get(f"{self.base_url}/job/{job_id}")
        resp.raise_for_status()
        return resp.json()

    def list_jobs(self) -> list[dict]:
        """List all jobs. GET /jobs/"""
        resp = self._client.get(f"{self.base_url}/jobs/")
        resp.raise_for_status()
        return resp.json()

    def wait_for_job(self, job_id: int, poll: float = 1.0, timeout: float = 3600.0) -> dict:
        """Poll until job completes or fails. Returns the final job dict."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get_job(job_id)
            status = job.get("status", "")
            if status in ("completed", "failed"):
                return job
            time.sleep(poll)
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

    # ── v1 Audio & Engine routes ──────────────────────────────────────

    def list_engines(self) -> dict:
        """List every engine the gateway exposes, with capabilities and status. GET /v1/engines"""
        resp = self._client.get(f"{self.base_url}/v1/engines")
        resp.raise_for_status()
        return resp.json()

    def create_transcription(
        self,
        audio_path: "Path | str",
        *,
        model: str = "whisper-1",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        response_format: str = "json",
        engine: Optional[str] = None,
        private: bool = False,
        profile: Optional[str] = None,
    ) -> dict:
        """Transcribe audio synchronously via the gateway. POST /v1/audio/transcriptions

        Returns:
            Dict with key ``text`` (default "json" format).
            With ``response_format="verbose_json"``, returns ``text``, ``language``, ``duration``, ``segments``.
        """
        audio_path = Path(audio_path)
        data: dict = {
            "model": model,
            "response_format": response_format,
            "private": str(private).lower(),
        }
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        if engine:
            data["engine"] = engine
        if profile:
            data["profile"] = profile
        with open(audio_path, "rb") as f:
            resp = self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f, "audio/wav")},
                data=data,
            )
        resp.raise_for_status()
        return resp.json()

    def create_speech(
        self,
        text: str,
        *,
        model: str = "tts-1",
        voice: str = "alloy",
        response_format: str = "mp3",
        speed: float = 1.0,
        engine: Optional[str] = None,
        private: bool = False,
    ) -> bytes:
        """Synthesise speech from text via the gateway. POST /v1/audio/speech

        Returns:
            Raw audio bytes in the requested format.
        """
        body: dict = {
            "input": text,
            "model": model,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
            "private": private,
        }
        if engine:
            body["engine"] = engine
        resp = self._client.post(
            f"{self.base_url}/v1/audio/speech",
            json=body,
        )
        resp.raise_for_status()
        return resp.content

    # ── Profile CRUD ──────────────────────────────────────────────────

    def list_profiles(self) -> list[dict]:
        """List stored profiles. GET /profiles"""
        resp = self._client.get(f"{self.base_url}/profiles")
        resp.raise_for_status()
        return resp.json()["profiles"]

    def get_profile(self, name: str) -> dict:
        """Fetch one profile. GET /profiles/{name}"""
        resp = self._client.get(f"{self.base_url}/profiles/{name}")
        resp.raise_for_status()
        return resp.json()

    def put_profile(self, name: str, profile: dict) -> dict:
        """Create or replace a profile. PUT /profiles/{name}"""
        resp = self._client.put(f"{self.base_url}/profiles/{name}", json=profile)
        resp.raise_for_status()
        return resp.json()

    def delete_profile(self, name: str) -> None:
        """Delete a profile. DELETE /profiles/{name}"""
        resp = self._client.delete(f"{self.base_url}/profiles/{name}")
        resp.raise_for_status()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
