"""Deepgram provider — POST raw audio bytes to /v1/listen."""
import logging
import mimetypes
from pathlib import Path

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/listen"
_TIMEOUT = 600.0
_OPTION_KEYS = {"smart_format", "diarize", "punctuate", "numerals"}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("deepgram: dropping unknown option '%s'", key)
    return kept


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the Deepgram REST API."""
    audio_path = Path(audio_path)
    params: dict = {"model": model or DEFAULT_MODELS["deepgram"]}
    if language:
        params["language"] = language
    params.update(_filter_options(options))

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    headers = {"Authorization": f"Token {api_key}", "Content-Type": content_type}

    resp = httpx.post(
        _URL,
        params=params,
        headers=headers,
        content=audio_path.read_bytes(),
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("deepgram", resp.status_code, resp.text)

    data = resp.json()
    alt = data["results"]["channels"][0]["alternatives"][0]
    text = alt.get("transcript", "")
    words = alt.get("words") or []
    start = float(words[0]["start"]) if words else 0.0
    end = float(words[-1]["end"]) if words else 0.0
    return TranscriptionResult(
        text=text,
        language=language or "",
        segments=[{"start": start, "end": end, "text": text}],
    )
