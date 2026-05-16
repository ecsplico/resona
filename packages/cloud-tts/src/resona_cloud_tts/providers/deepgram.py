"""Deepgram provider — JSON POST to /v1/speak."""
import logging

import httpx

from ..errors import CloudTTSError, ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS
from ..types import SpeechResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/speak"
_TIMEOUT = 600.0
_OPTION_KEYS = {"sample_rate"}

# response_format -> Deepgram ``encoding`` query value.
_ENCODINGS = {
    "mp3": "mp3",
    "opus": "opus",
    "flac": "flac",
    "aac": "aac",
    "wav": "linear16",
}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted query keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("deepgram: dropping unknown option '%s'", key)
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
    """Synthesize ``text`` to speech via the Deepgram speak API.

    Deepgram encodes the voice in the model name; an explicit ``voice``
    argument overrides ``model``.
    """
    if response_format not in _ENCODINGS:
        raise CloudTTSError(
            f"deepgram: unsupported response_format '{response_format}'"
        )

    params: dict = {
        "model": voice or model or DEFAULT_MODELS["deepgram"],
        "encoding": _ENCODINGS[response_format],
    }
    if response_format == "wav":
        params["container"] = "wav"
    params.update(_filter_options(options))

    resp = httpx.post(
        _URL,
        headers={"Authorization": f"Token {api_key}"},
        params=params,
        json={"text": text},
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("deepgram", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
