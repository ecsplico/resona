"""Live (WebSocket) streaming STT — normalized provider sessions.

Batch transcription POSTs a whole file (``providers/*.transcribe``); this module
adds the realtime path. :func:`open_stream` returns a provider session that:

* ``await session.send_audio(pcm_bytes)`` — push 16-bit little-endian PCM,
* ``await session.finish()`` — signal end-of-audio (flush/commit upstream),
* ``async for t in session`` — yield :class:`StreamTranscript` updates,
* ``await session.close()`` — tear down the connection.

Providers normalize their wire shapes to ``StreamTranscript`` so callers (the
resona-api ``WS /v1/listen`` bridge) stay provider-agnostic. OpenAI's realtime
API is not wired, so only Deepgram and ElevenLabs are streaming-capable.
"""
import logging
from dataclasses import dataclass

from .registry import get_provider

log = logging.getLogger(__name__)

STREAMING_PROVIDERS: set[str] = {"deepgram", "elevenlabs"}


@dataclass
class StreamTranscript:
    """One transcript update from a streaming provider.

    ``text`` is the provider's transcript for the current segment (cumulative
    within an utterance, not a delta); ``is_final`` marks a committed/finalized
    segment vs an in-progress (interim) hypothesis.
    """
    text: str
    is_final: bool


def supports_streaming(provider: str) -> bool:
    """True when ``provider`` has a realtime WebSocket STT API wired here."""
    return provider in STREAMING_PROVIDERS


async def open_stream(
    provider: str,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    sample_rate: int = 16000,
    interim_results: bool = False,
    options: dict | None = None,
):
    """Open a live STT session for ``provider``.

    Returns a session object (see module docstring). Raises ``ValueError`` for a
    provider without a streaming API.
    """
    if not supports_streaming(provider):
        raise ValueError(f"provider '{provider}' does not support live streaming")
    mod = get_provider(provider)
    return await mod.open_stream(
        api_key=api_key,
        model=model,
        language=language,
        sample_rate=sample_rate,
        interim_results=interim_results,
        options=options,
    )
