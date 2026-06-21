"""OpenAI-compatible /v1/audio/* endpoints and /v1/engines discovery."""
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from . import engine_registry as reg
from .auth import verify_api_key
from .endpoints import validate_audio_file
from .paths import PROFILES_PATH
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import resolve_profile, ProfileError

log = logging.getLogger(__name__)
router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    """Map a registry or provider error to an HTTPException."""
    if isinstance(exc, (reg.EngineNotFoundError, reg.CapabilityError,
                        reg.PrivacyViolationError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, reg.EngineUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, reg.NoEngineError):
        return HTTPException(status_code=409, detail=str(exc))
    name = type(exc).__name__
    if name == "MissingAPIKeyError":
        return HTTPException(status_code=503, detail=str(exc))
    if name == "ProviderHTTPError":
        return HTTPException(status_code=502, detail=str(exc))
    if name in ("CloudTTSError", "CloudSTTError"):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/v1/engines", tags=["Engines"])
def list_engines(api_key: str = Depends(verify_api_key)):
    """List every engine this gateway exposes, with capabilities and status."""
    catalogue = reg.get_catalogue(fresh=True)
    return {
        "engines": [
            {
                "name": e.name,
                "kind": e.kind,
                "capabilities": e.capabilities,
                "private": e.private,
                "available": e.available,
                "models": e.models,
            }
            for e in catalogue
        ],
        "default": reg.effective_default("stt", catalogue=catalogue),
    }


@router.post("/v1/audio/transcriptions", tags=["Audio"])
async def create_transcription(
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    language: str = Form(default="de"),
    prompt: str = Form(default=""),
    temperature: float | None = Form(default=None),
    response_format: str = Form(default="json"),
    engine: str | None = Form(default=None),
    private: bool = Form(default=False),
    profile: str | None = Form(default=None),
    api_key: str = Depends(verify_api_key),
):
    """OpenAI-compatible synchronous speech-to-text."""
    validate_audio_file(file)
    try:
        info = reg.resolve(engine, "stt", private)
    except reg.EngineError as exc:
        raise _http_error(exc)

    suffix = Path(file.filename or "audio").suffix or ".bin"
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = reg.run_stt(
            info, tmp_path, language=language, model=model, prompt=prompt
        )
    except Exception as exc:
        raise _http_error(exc)
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        prof = resolve_profile(profile or "default", PROFILES_PATH)
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {exc}")
    pp = build_pipeline(prof).run(result.get("text", ""))
    text = pp.text

    if response_format == "text":
        return PlainTextResponse(text)
    if response_format == "verbose_json":
        segments = result.get("segments", [])
        duration = segments[-1]["end"] if segments else 0.0
        payload: dict = {
            "text": text,
            "language": result.get("language", language),
            "duration": duration,
            "segments": segments,
        }
        if pp.data:
            payload["structured"] = pp.data
        return JSONResponse(payload)
    payload = {"text": text}
    if pp.data:
        payload["structured"] = pp.data
    return JSONResponse(payload)


class SpeechRequest(BaseModel):
    """Request body for POST /v1/audio/speech."""

    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    speed: float | None = None
    engine: str | None = None
    private: bool = False
    language: str | None = None


@router.post("/v1/audio/speech", tags=["Audio"])
def create_speech(
    body: SpeechRequest,
    api_key: str = Depends(verify_api_key),
):
    """OpenAI-compatible synchronous text-to-speech (local or cloud engines)."""
    try:
        info = reg.resolve(body.engine, "tts", body.private)
    except reg.EngineError as exc:
        raise _http_error(exc)
    try:
        result = reg.run_tts(
            info,
            body.input,
            model=body.model,
            voice=body.voice,
            response_format=body.response_format,
            speed=body.speed,
            language=body.language,
        )
    except Exception as exc:
        raise _http_error(exc)
    return StreamingResponse(
        iter([result["audio"]]), media_type=result["content_type"]
    )
