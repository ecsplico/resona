"""Deepgram provider — batch POST to /v1/listen, plus a live streaming session."""
import json
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..errors import ProviderHTTPError
from ..keyterms import normalize_keyterms
from ..registry import DEFAULT_MODELS
from ..streaming import StreamTranscript
from ..types import TranscriptionResult

log = logging.getLogger(__name__)

_URL = "https://api.deepgram.com/v1/listen"
_WS_URL = "wss://api.deepgram.com/v1/listen"
_TIMEOUT = 600.0
_OPTION_KEYS = {"smart_format", "diarize", "punctuate", "numerals", "utterances"}


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


def _keyterm_param(model: str) -> str:
    """Deepgram's biasing parameter for ``model``.

    Nova-3 and Flux take ``keyterm`` (keyterm prompting); older models (Nova-2,
    Enhanced, Base) only know ``keywords``.
    """
    return "keyterm" if model.startswith(("nova-3", "flux")) else "keywords"


def _add_keyterms(params: dict, model: str, keyterms: Sequence[str]) -> None:
    """Add each normalized term as a repeated query param (httpx / ``urlencode(doseq=True)``)."""
    terms = normalize_keyterms(keyterms, provider="deepgram")
    if terms:
        params[_keyterm_param(model)] = terms


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
    keyterms: Sequence[str] = (),
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the Deepgram REST API.

    Segments are Deepgram's utterances when ``options`` asks for them (``utterances``),
    else one segment over the whole transcript. Each segment carries Whisper-style
    ``words`` (``word`` with its leading space, ``start``, ``end``) and, with ``diarize``,
    every word's 0-based Deepgram ``speaker``. ``keyterms`` go out as repeated
    ``keyterm`` params (Nova-3/Flux) or ``keywords`` (older models).
    """
    audio_path = Path(audio_path)
    model = model or DEFAULT_MODELS["deepgram"]
    params: dict = {"model": model}
    if language:
        params["language"] = language
    params.update(_filter_options(options))
    _add_keyterms(params, model, keyterms)

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
    utterances = data["results"].get("utterances") or []
    if utterances:
        segments = [
            {
                "start": float(u["start"]),
                "end": float(u["end"]),
                "text": u.get("transcript", ""),
                "words": [_word(w) for w in u.get("words") or []],
            }
            for u in utterances
        ]
    else:
        words = alt.get("words") or []
        start = float(words[0]["start"]) if words else 0.0
        end = float(words[-1]["end"]) if words else 0.0
        segment: dict = {"start": start, "end": end, "text": text}
        if words:
            segment["words"] = [_word(w) for w in words]
        segments = [segment]
    return TranscriptionResult(text=text, language=language or "", segments=segments)


def _word(raw: dict) -> dict:
    """One Deepgram word as a Whisper-style word dict (leading space, punctuated when available)."""
    word = {
        "start": float(raw["start"]),
        "end": float(raw["end"]),
        "word": " " + str(raw.get("punctuated_word") or raw.get("word", "")),
    }
    if raw.get("speaker") is not None:
        word["speaker"] = int(raw["speaker"])
    return word


# ── Live streaming ───────────────────────────────────────────────────

async def open_stream(
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    sample_rate: int = 16000,
    interim_results: bool = False,
    options: dict | None = None,
    keyterms: Sequence[str] = (),
) -> "_DeepgramStream":
    """Open a Deepgram live WebSocket and return a normalized session."""
    import websockets  # lazy: streaming-only dependency

    model = model or DEFAULT_MODELS["deepgram"]
    params: dict = {
        "model": model,
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
    _add_keyterms(params, model, keyterms)

    url = f"{_WS_URL}?{urlencode(params, doseq=True)}"
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
