"""
WhisperClient — HTTP client for the whisper-server API.

Configuration (in priority order):
  1. Explicit base_url / api_key arguments
  2. WS_API_URL / WS_API_KEY environment variables
  3. ~/.whisper-server/config.json (via WhisperClient.from_config())
"""
import os
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class WhisperClient:
    """HTTP client for ws-api and ws-engine."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
    ):
        self.base_url = (base_url or os.getenv("WS_API_URL", "http://localhost:7000")).rstrip("/")
        self.api_key = api_key or os.getenv("WS_API_KEY", "")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"X-API-Key": self.api_key} if self.api_key else {},
        )

    @classmethod
    def from_config(cls, auto_start: bool = True, timeout: float = 300.0) -> "WhisperClient":
        """
        Create a client by resolving the backend to use:

        1. WS_API_URL env var (if set) — used directly, no config lookup.
        2. First reachable backend in ~/.whisper-server/config.json.
        3. If none reachable and a backend has compose_dir set, start it via
           docker compose up -d and wait for it to become healthy.

        Raises RuntimeError if no backend could be resolved.
        """
        env_url = os.getenv("WS_API_URL")
        if env_url:
            return cls(base_url=env_url, timeout=timeout)

        from .config import resolve_backend
        entry = resolve_backend(auto_start=auto_start)
        if entry:
            return cls(base_url=entry.api_url, api_key=entry.api_key, timeout=timeout)

        raise RuntimeError(
            "No reachable whisper-server backend found.\n"
            "Add one with:  ws-cli backends add <name> <url>"
        )

    # ── Job API operations ────────────────────────────────────────────

    def submit_job(
        self,
        filepath: Path | str,
        keep: bool = True,
        translate: bool = False,
    ) -> dict:
        """Upload an audio file and register it for async transcription. POST /jobs"""
        filepath = Path(filepath)
        with open(filepath, "rb") as f:
            resp = self._client.post(
                f"{self.base_url}/jobs",
                files={"audio_files": (filepath.name, f, "audio/wav")},
                data={"keep": str(keep).lower(), "translate": str(translate).lower()},
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

    # ── Replacement CRUD ──────────────────────────────────────────────

    def list_replacements(self) -> list[dict]:
        resp = self._client.get(f"{self.base_url}/replacements/")
        resp.raise_for_status()
        return resp.json()

    def add_replacement(self, name: str, replacement: str) -> dict:
        resp = self._client.post(
            f"{self.base_url}/replacements/",
            json={"name": name, "replacement": replacement},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_replacement(self, replacement_id: int) -> None:
        resp = self._client.delete(f"{self.base_url}/replacements/{replacement_id}")
        resp.raise_for_status()

    # ── Prompt CRUD ───────────────────────────────────────────────────

    def list_prompts(self) -> list[dict]:
        resp = self._client.get(f"{self.base_url}/prompts/")
        resp.raise_for_status()
        return resp.json()

    def add_prompt(self, phrase: str) -> dict:
        resp = self._client.post(
            f"{self.base_url}/prompts/",
            json={"phrase": phrase},
        )
        resp.raise_for_status()
        return resp.json()

    def activate_prompt(self, prompt_id: int) -> None:
        resp = self._client.put(f"{self.base_url}/prompts/{prompt_id}/activate")
        resp.raise_for_status()

    def deactivate_prompt(self, prompt_id: int) -> None:
        resp = self._client.put(f"{self.base_url}/prompts/{prompt_id}/deactivate")
        resp.raise_for_status()

    def remove_prompt(self, prompt_id: int) -> None:
        resp = self._client.delete(f"{self.base_url}/prompts/{prompt_id}")
        resp.raise_for_status()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
