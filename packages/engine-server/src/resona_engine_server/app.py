"""
resona-engine-server: Stateless FastAPI transcription service.

Endpoints:
  GET  /health
  POST /transcribe
  WS   /ws/transcribe
  WS   /ws/live
"""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from decouple import config
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware

from .auth import verify_api_key
from resona_asr_core.registry import get_transcriber
from resona_asr_core.audio import load_audio, SAMPLE_RATE
from .ws_transcribe import transcribe_websocket
from .ws_live import live_transcribe_websocket

log = logging.getLogger(__name__)

_model_lock = threading.Lock()


def _run_asr(file, task: str = "transcribe", language: str = "de", **asr_options) -> dict:
    """Load audio and run transcription using the registered backend."""
    if isinstance(file, str):
        with open(file, "rb") as f:
            audio = load_audio(f, sr=SAMPLE_RATE)
    else:
        audio = load_audio(file, sr=SAMPLE_RATE)

    options = {"task": task, "language": language, **asr_options}

    with _model_lock:
        transcriber = get_transcriber()
        result = transcriber.transcribe(audio, **options)

    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load the ASR model before accepting requests."""
    log.info("Pre-loading ASR model at startup...")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, get_transcriber)
        log.info("ASR model loaded and ready.")
    except Exception as e:
        log.warning(f"Model pre-load failed ({e}) — will load on first request.")
    yield


app = FastAPI(
    title="resona-engine",
    description="Stateless transcription engine",
    lifespan=lifespan,
)

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
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    audio_file: UploadFile = File(...),
    task: str = Form(default="transcribe"),
    language: str = Form(default="de"),
    initial_prompt: Optional[str] = Form(default=None),
    output: Optional[str] = Form(default=None),
    vad_filter: bool = Form(default=False),
    word_timestamps: bool = Form(default=False),
    api_key: Optional[str] = Depends(verify_api_key),
):
    """Transcribe an audio file. Returns raw text, language, and segments.
    No replacements — postprocessing is caller-side.
    """
    asr_options = {
        "vad_filter": vad_filter,
        "word_timestamps": word_timestamps,
    }
    if initial_prompt:
        asr_options["initial_prompt"] = initial_prompt

    result = _run_asr(audio_file.file, task=task, language=language, **asr_options)

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

    return {
        "text": result.get("text", ""),
        "language": result.get("language", language),
        "segments": serializable_segments,
    }


@app.websocket("/ws/transcribe")
async def websocket_transcribe_endpoint(websocket: WebSocket):
    try:
        await transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
        raise


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    try:
        await live_transcribe_websocket(websocket)
    except Exception as e:
        log.error(f"Live WebSocket error: {e}", exc_info=True)
        raise
