"""HTTP client for calling the resona-engine transcription service."""
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


class EngineClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=3600.0)

    def transcribe(
        self,
        filepath: Path | str,
        language: str = "de",
        initial_prompt: str = "",
        task: str = "transcribe",
    ) -> dict:
        """Call engine POST /transcribe and return the JSON result."""
        filepath = Path(filepath)
        with open(filepath, "rb") as f:
            data = {
                "task": task,
                "language": language,
            }
            if initial_prompt:
                data["initial_prompt"] = initial_prompt

            resp = self.client.post(
                f"{self.base_url}/transcribe",
                files={"audio_file": (filepath.name, f, "audio/wav")},
                data=data,
            )

        resp.raise_for_status()
        return resp.json()

    def health(self) -> bool:
        """Check if the engine is healthy."""
        try:
            resp = self.client.get(f"{self.base_url}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        self.client.close()
