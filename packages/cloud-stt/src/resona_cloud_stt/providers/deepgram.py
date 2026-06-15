"""Deepgram provider — batch POST to /v1/listen, plus a live streaming session."""
import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..errors import ProviderHTTPError
from ..registry import DEFAULT_MODELS
from ..streaming import StreamTranscript
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/listen"
_WS_URL = "wss://api.deepgram.com/v1/listen"
_TIMEOUT = 600.0
_OPTION_KEYS = {"smart_format", "diarize", "punctuate", "numerals"}


def _qval(value) -> str:
    """Render a query-param value the Deepgram way (lowercase booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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


# ── Live streaming ───────────────────────────────────────────────────

async def open_stream(
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    sample_rate: int = 16000,
    interim_results: bool = False,
    options: dict | None = None,
) -> "_DeepgramStream":
    """Open a Deepgram live WebSocket and return a normalized session."""
    import websockets  # lazy: streaming-only dependency

    params: dict = {
        "model": model or DEFAULT_MODELS["deepgram"],
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
        "channels": "1",
        "interim_results": "true" if interim_results else "false",
        "punctuate": "true",
    }
    if language:
        params["language"] = language
    for key, value in _filter_options(options).items():
        params[key] = _qval(value)

    url = f"{_WS_URL}?{urlencode(params)}"
    ws = await websockets.connect(
        url,
        additional_headers={"Authorization": f"Token {api_key}"},
        max_size=None,
    )
    return _DeepgramStream(ws)


class _DeepgramStream:
    """Normalized Deepgram live session: binary PCM up, ``Results`` down."""

    def __init__(self, ws):
        self._ws = ws

    async def send_audio(self, pcm: bytes) -> None:
        await self._ws.send(pcm)  # Deepgram takes raw binary frames

    async def finish(self) -> None:
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001 - already closing
            pass

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    def __aiter__(self):
        return self._results()

    async def _results(self):
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if msg.get("type") != "Results":
                continue  # Metadata / UtteranceEnd / SpeechStarted ignored
            alternatives = msg.get("channel", {}).get("alternatives") or []
            text = alternatives[0].get("transcript", "") if alternatives else ""
            yield StreamTranscript(text=text, is_final=bool(msg.get("is_final")))
