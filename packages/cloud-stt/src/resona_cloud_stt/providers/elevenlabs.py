"""ElevenLabs provider — batch multipart POST, plus a live streaming session."""
import base64
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

_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
_TIMEOUT = 600.0
_OPTION_KEYS = {"diarize", "num_speakers", "tag_audio_events"}

# Sample rates ElevenLabs accepts as a pcm_<rate> audio_format.
_PCM_RATES = {8000, 16000, 22050, 24000, 44100, 48000}

# Realtime caps keyterms at 1000 characters in total (batch has no total cap).
_REALTIME_KEYTERM_CHARS = 1000


def _keyterms_for(model: str, keyterms: Sequence[str]) -> list[str]:
    """Normalized ``keyterms`` for ``model``; none for Scribe v1, which has no keyterm prompting."""
    if not keyterms:
        return []
    if model.startswith("scribe_v1"):
        log.info("elevenlabs: %s has no keyterm prompting; keyterms not sent", model)
        return []
    return normalize_keyterms(keyterms, provider="elevenlabs")


def _audio_format(sample_rate: int) -> str:
    """Map a PCM sample rate to ElevenLabs' ``audio_format`` token."""
    if sample_rate in _PCM_RATES:
        return f"pcm_{sample_rate}"
    log.warning("elevenlabs: unsupported sample_rate %s, defaulting to pcm_16000", sample_rate)
    return "pcm_16000"


def _filter_options(options: dict | None) -> dict:
    """Keep only whitelisted keys; warn and drop the rest."""
    if not options:
        return {}
    kept: dict = {}
    for key, value in options.items():
        if key in _OPTION_KEYS:
            kept[key] = value
        else:
            log.warning("elevenlabs: dropping unknown option '%s'", key)
    return kept


def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
    keyterms: Sequence[str] = (),
) -> TranscriptionResult:
    """Transcribe ``audio_path`` via the ElevenLabs REST API.

    ``keyterms`` go out as repeated ``keyterms`` form fields (Scribe v2 and later;
    dropped for Scribe v1).
    """
    audio_path = Path(audio_path)
    model = model or DEFAULT_MODELS["elevenlabs"]
    data: dict = {"model_id": model}
    if language:
        data["language_code"] = language
    for key, value in _filter_options(options).items():
        data[key] = str(value)
    if terms := _keyterms_for(model, keyterms):
        data["keyterms"] = terms  # a list → one multipart field per term

    content_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    with open(audio_path, "rb") as fh:
        resp = httpx.post(
            _URL,
            headers={"xi-api-key": api_key},
            data=data,
            files={"file": (audio_path.name, fh, content_type)},
            timeout=_TIMEOUT,
        )
    if resp.status_code >= 300:
        raise ProviderHTTPError("elevenlabs", resp.status_code, resp.text)

    body = resp.json()
    text = body.get("text", "")
    words = body.get("words") or []
    start = float(words[0]["start"]) if words else 0.0
    end = float(words[-1]["end"]) if words else 0.0
    return TranscriptionResult(
        text=text,
        language=body.get("language_code", ""),
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
    keyterms: Sequence[str] = (),
) -> "_ElevenLabsStream":
    """Open an ElevenLabs realtime STT WebSocket and return a normalized session.

    Uses ``commit_strategy=vad`` so the server auto-commits speech segments into
    final transcripts (with interim ``partial_transcript`` updates in between),
    matching the Deepgram-style finals/interims the bridge expects. ``keyterms``
    go out as repeated ``keyterms`` query params (Scribe v2 Realtime and later,
    1000 characters in total).
    """
    import websockets  # lazy: streaming-only dependency

    model = model or DEFAULT_MODELS["elevenlabs"]
    params: dict = {
        "model_id": model,
        "audio_format": _audio_format(sample_rate),
        "commit_strategy": "vad",
    }
    if language:
        params["language_code"] = language
    terms: list[str] = []
    total = 0
    for term in _keyterms_for(model, keyterms):
        if total + len(term) > _REALTIME_KEYTERM_CHARS:
            break
        terms.append(term)
        total += len(term)
    if terms:
        params["keyterms"] = terms

    url = f"{_WS_URL}?{urlencode(params, doseq=True)}"
    ws = await websockets.connect(
        url,
        additional_headers={"xi-api-key": api_key},
        max_size=None,
    )
    return _ElevenLabsStream(ws, sample_rate=sample_rate)


class _ElevenLabsStream:
    """Normalized ElevenLabs realtime session: base64 PCM chunks up, transcripts down."""

    def __init__(self, ws, *, sample_rate: int):
        self._ws = ws
        self._sample_rate = sample_rate

    async def send_audio(self, pcm: bytes) -> None:
        await self._ws.send(json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": False,
            "sample_rate": self._sample_rate,
        }))

    async def finish(self) -> None:
        # Force a final commit of whatever audio is still buffered server-side.
        try:
            await self._ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": "",
                "commit": True,
                "sample_rate": self._sample_rate,
            }))
        except Exception:  # noqa: BLE001
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
            mtype = msg.get("message_type")
            if mtype == "partial_transcript":
                yield StreamTranscript(text=msg.get("text", ""), is_final=False)
            elif mtype in ("committed_transcript", "committed_transcript_with_timestamps"):
                yield StreamTranscript(text=msg.get("text", ""), is_final=True)
            elif mtype and mtype.endswith("error"):
                log.error("elevenlabs stream error: %s", msg.get("error") or msg)
            # session_started and unknown types ignored
