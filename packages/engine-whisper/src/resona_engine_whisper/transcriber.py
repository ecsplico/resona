"""OpenAI Whisper (PyTorch) transcription backend for Resona."""

import logging

import numpy as np
import whisper
from decouple import config

from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_WHISPER_MODEL", default="large-v3")


class WhisperTranscriber:
    """Original OpenAI Whisper backend (PyTorch).

    Heavier than faster-whisper but supports the full Whisper API surface.
    Configure model via DEFAULT_WHISPER_MODEL env var.
    """

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        log.info(f"Loading Whisper model: {model_name} on {device}...")
        self.model = whisper.load_model(model_name, device=device)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult:
        opts = {
            "language": language,
            "task": task,
            "word_timestamps": word_timestamps,
            **kwargs,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        result = self.model.transcribe(audio, **opts)

        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", language),
            segments=result.get("segments", []),
        )
