"""Faster-whisper (CTranslate2) transcription backend for Resona."""

import logging

import numpy as np
from decouple import config
from faster_whisper import WhisperModel

from resona_engine_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_FASTWHISPER_MODEL", default="large-v3")


class FastWhisperTranscriber:
    """CTranslate2-based Whisper backend. Default and recommended."""

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        compute_type = "int8_float16" if device == "cuda" else "int8"
        log.info(f"Loading FastWhisper model: {model_name} on {device} ({compute_type})")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

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
            "vad_filter": vad_filter,
            "beam_size": kwargs.get("beam_size", 5),
            **{k: v for k, v in kwargs.items() if k != "beam_size"},
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        segment_gen, info = self.model.transcribe(audio, **opts)
        segments = list(segment_gen)
        text = "".join(seg.text for seg in segments)

        return TranscriptionResult(
            text=text,
            language=info.language,
            segments=segments,
        )
