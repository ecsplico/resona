"""OpenAI provider — JSON POST to /v1/audio/speech."""
import logging

import httpx

from ..errors import ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS, DEFAULT_VOICES
from ..types import SpeechResult

log = logging.getLogger(__name__)

_URL = "https://api.openai.com/v1/audio/speech"
_TIMEOUT = 600.0
_OPTION_KEYS = {"speed"}


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


def synthesize(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    options: dict | None = None,
) -> SpeechResult:
    """Synthesize ``text`` to speech via the OpenAI audio speech API."""
    if response_format not in CONTENT_TYPES:
        from ..errors import CloudTTSError
        raise CloudTTSError(f"openai: unsupported response_format '{response_format}'")

    body: dict = {
        "model": model or DEFAULT_MODELS["openai"],
        "input": text,
        "voice": voice or DEFAULT_VOICES["openai"],
        "response_format": response_format,
    }
    body.update(_filter_options(options))

    resp = httpx.post(
        _URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("openai", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
