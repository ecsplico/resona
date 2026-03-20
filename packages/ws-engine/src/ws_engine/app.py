"""
ws-engine: Stateless FastAPI transcription service.

Endpoints:
  GET  /health
  POST /transcribe
  WS   /ws/transcribe
  WS   /ws/live
"""
import json
import logging
from typing import Optional, List

from decouple import config
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware

from .auth import verify_api_key
from .utils import run_asr, load_audio
from .replacements import apply_replacements
from .ws_transcribe import transcribe_websocket
from .ws_live import live_transcribe_websocket

log = logging.getLogger(__name__)

app = FastAPI(title="ws-engine", description="Stateless transcription engine")

CORS_ORIGINS = config("CORS_ORIGINS", default="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    audio_file: UploadFile = File(...),
    task: str = Form(default="transcribe"),
    language: str = Form(default="de"),
    initial_prompt: Optional[str] = Form(default=None),
    replacements: Optional[str] = Form(default=None),  # JSON array of {name, replacement}
    output: Optional[str] = Form(default=None),
    vad_filter: bool = Form(default=False),
    word_timestamps: bool = Form(default=False),
    api_key: Optional[str] = Depends(verify_api_key),
):
    """
    Transcribe an audio file. Returns JSON with text, language, segments, and optionally md.

    replacements: JSON array of {"name": "<regex>", "replacement": "<text>"} objects.
    If provided, the text is processed through replacements and returned as 'md'.
    """
    asr_options = {
        "vad_filter": vad_filter,
        "word_timestamps": word_timestamps,
    }
    if initial_prompt:
        asr_options["initial_prompt"] = initial_prompt

    result = run_asr(audio_file.file, task=task, language=language, **asr_options)

    # Serialize segments for JSON response
    raw_segments = result.get("segments", [])
    serializable_segments = []
    for seg in raw_segments:
        if isinstance(seg, dict):
            serializable_segments.append(seg)
        else:
            try:
                d = {"start": seg.start, "end": seg.end, "text": seg.text}
                if word_timestamps and hasattr(seg, "words") and seg.words:
                    d["words"] = [
                        {"word": w.word, "start": w.start, "end": w.end}
                        for w in seg.words
                    ]
                serializable_segments.append(d)
            except AttributeError:
                serializable_segments.append({"text": str(seg)})

    response = {
        "text": result.get("text", ""),
        "language": result.get("language", language),
        "segments": serializable_segments,
    }

    # Apply replacements if provided
    if replacements:
        try:
            replacement_list = json.loads(replacements)
            if isinstance(replacement_list, list):
                md = apply_replacements(result.get("text", ""), replacement_list)
                response["md"] = md
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"Could not parse replacements: {e}")

    return response


@app.websocket("/ws/transcribe")
async def websocket_transcribe_endpoint(websocket: WebSocket):
    """WebSocket endpoint for streaming transcription."""
    try:
        await transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
        raise


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """WebSocket endpoint for live transcription with VAD."""
    try:
        await live_transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"Live WebSocket error: {e}", exc_info=True)
        raise
