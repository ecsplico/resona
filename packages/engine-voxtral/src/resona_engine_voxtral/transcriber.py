"""HuggingFace Transformers ASR backend for Resona.

Supports any model compatible with the transformers automatic-speech-recognition
pipeline, including Whisper variants and Voxtral. Passes initial_prompt directly
to models that support it.
"""

import logging

import numpy as np
from decouple import config
from transformers import pipeline as hf_pipeline

from resona_engine_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_VOXTRAL_MODEL", default="openai/whisper-large-v3")


class VoxtralTranscriber:
    """HuggingFace Transformers ASR pipeline backend.

    Uses transformers.pipeline("automatic-speech-recognition") with 30-second
    chunked inference. Supports initial_prompt on models that accept it.

    Configure model via DEFAULT_VOXTRAL_MODEL env var.
    """

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        log.info(f"Loading Transformers ASR model: {model_name} on {device}")
        self.model = hf_pipeline(
            "automatic-speech-recognition",
            model=model_name,
            device=device,
            chunk_length_s=30,
        )

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
        pipeline_kwargs = {
            "return_timestamps": True,
            "generate_kwargs": {"language": language, "task": task},
        }

        if initial_prompt:
            pipeline_kwargs["initial_prompt"] = initial_prompt

        output = self.model(audio, **pipeline_kwargs)

        segments = []
        for chunk in output.get("chunks", []):
            seg = {"text": chunk.get("text", "")}
            ts = chunk.get("timestamp")
            if ts and len(ts) == 2:
                seg["start"] = ts[0]
                seg["end"] = ts[1]
            segments.append(seg)

        return TranscriptionResult(
            text=output.get("text", ""),
            language=language,
            segments=segments,
        )
