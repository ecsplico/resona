"""OpenAI-compatible /v1/audio/* endpoints and /v1/engines discovery."""
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from resona_job_core import PolicyDenied
from resona_postprocess.profile import ProfileError
from sqlmodel import Session

from . import engine_registry as reg
from .auth import Principal, client_principal
from .db.engine import engine as db_engine
from .deps import validate_audio_file
from .privacy import ProfileNotFound, admit, admit_speech, is_profile_name

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


def _policy_denied(exc: PolicyDenied) -> JSONResponse:
    """A denied stage never reaches an engine or provider; report it as 403."""
    return JSONResponse(
        status_code=403,
        content={"error": "policy_denied", "stage": exc.stage, "reason": exc.reason},
    )


@router.get("/v1/engines", tags=["Engines"])
def list_engines(principal: Principal = Depends(client_principal)):
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
    language: str | None = Form(default=None),
    prompt: str = Form(default=""),
    temperature: float | None = Form(default=None),
    response_format: str = Form(default="json"),
    engine: str | None = Form(default=None),
    private: bool = Form(default=True),
    profile: str | None = Form(default=None),
    principal: Principal = Depends(client_principal),
):
    """OpenAI-compatible synchronous speech-to-text.

    The whole chain (ASR engine and every postprocess stage) is admitted
    against the organization's policy before the upload is processed.
    """
    validate_audio_file(file)
    if profile is not None and not is_profile_name(profile):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_profile", "message": "profile must be a stored profile name"},
        )
    try:
        with Session(db_engine) as session:
            admission = admit(
                session,
                principal.organization_id,
                private=private,
                engine=engine,
                profile=profile,
                language=language,
            )
    except PolicyDenied as exc:
        return _policy_denied(exc)
    except ProfileNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Unknown profile: {exc}")
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {exc}")
    except reg.EngineError as exc:
        raise _http_error(exc)

    suffix = Path(file.filename or "audio").suffix or ".bin"
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = reg.run_stt(
            admission.engine, tmp_path,
            language=admission.language, model=model, prompt=prompt,
        )
    except Exception as exc:
        raise _http_error(exc)
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        pp = admission.pipeline.run(result.get("text", ""))
    except PolicyDenied as exc:
        return _policy_denied(exc)
    text = pp.text

    if response_format == "text":
        return PlainTextResponse(text)
    if response_format == "verbose_json":
        segments = result.get("segments", [])
        duration = segments[-1]["end"] if segments else 0.0
        payload: dict = {
            "text": text,
            "language": result.get("language", admission.language),
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
    private: bool = True
    language: str | None = None


@router.post("/v1/audio/speech", tags=["Audio"])
def create_speech(
    body: SpeechRequest,
    principal: Principal = Depends(client_principal),
):
    """OpenAI-compatible synchronous text-to-speech (local or cloud engines)."""
    try:
        with Session(db_engine) as session:
            info = admit_speech(
                session,
                principal.organization_id,
                private=body.private,
                engine=body.engine,
            )
    except PolicyDenied as exc:
        return _policy_denied(exc)
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
