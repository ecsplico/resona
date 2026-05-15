"""OpenAI provider — multipart POST to /v1/audio/transcriptions."""
import logging
import mimetypes
from pathlib import Path

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.openai.com/v1/audio/transcriptions"
_TIMEOUT = 600.0
_OPTION_KEYS = {"prompt", "temperature"}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("openai: dropping unknown option '%s'", key)
    return kept


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the OpenAI audio transcriptions API."""
    audio_path = Path(audio_path)
    data: dict = {
        "model": model or DEFAULT_MODELS["openai"],
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    for key, value in _filter_options(options).items():
        data[key] = str(value)

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    with open(audio_path, "rb") as fh:
        resp = httpx.post(
            _URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (audio_path.name, fh, content_type)},
            timeout=_TIMEOUT,
        )
    if resp.status_code >= 300:
        raise ProviderHTTPError("openai", resp.status_code, resp.text)

    body = resp.json()
    segments = [
        {"start": float(s.get("start", 0.0)),
         "end": float(s.get("end", 0.0)),
         "text": s.get("text", "")}
        for s in body.get("segments") or []
    ]
    return TranscriptionResult(
        text=body.get("text", ""),
        language=body.get("language", ""),
        segments=segments,
    )
