"""ElevenLabs provider — JSON POST to /v1/text-to-speech/{voice_id}."""
import logging

import httpx

from ..errors import CloudTTSError, ProviderHTTPError
from ..registry import CONTENT_TYPES, DEFAULT_MODELS, DEFAULT_VOICES
from ..types import SpeechResult

log = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
_TIMEOUT = 600.0
_OPTION_KEYS = {"stability", "similarity_boost", "style"}

# ElevenLabs only supports a subset of formats; map to its output_format ids.
_OUTPUT_FORMATS = {
    "mp3": "mp3_44100_128",
    "opus": "opus_48000_128",
}


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted voice_settings keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("elevenlabs: dropping unknown option '%s'", key)
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
    """Synthesize ``text`` to speech via the ElevenLabs text-to-speech API."""
    if response_format not in _OUTPUT_FORMATS:
        raise CloudTTSError(
            f"elevenlabs: unsupported response_format '{response_format}' "
            f"(supported: {sorted(_OUTPUT_FORMATS)})"
        )

    voice_id = voice or DEFAULT_VOICES["elevenlabs"]
    body: dict = {
        "text": text,
        "model_id": model or DEFAULT_MODELS["elevenlabs"],
    }
    voice_settings = _filter_options(options)
    if voice_settings:
        body["voice_settings"] = voice_settings

    resp = httpx.post(
        f"{_BASE}/{voice_id}",
        headers={"xi-api-key": api_key},
        params={"output_format": _OUTPUT_FORMATS[response_format]},
        json=body,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 300:
        raise ProviderHTTPError("elevenlabs", resp.status_code, resp.text)

    return SpeechResult(
        audio=resp.content,
        content_type=CONTENT_TYPES[response_format],
    )
