"""Apple Silicon (MLX/Metal) Whisper transcription backend for Resona.

MLX runs Whisper on the Apple Silicon GPU (Metal) without CUDA or PyTorch,
making it the recommended local engine on macOS. The model is an
``mlx-community/...`` Hugging Face repo (weights are downloaded and cached on
first use). ``whisper-large-v3-turbo`` is a good German+English default.
"""

import logging

import numpy as np
from decouple import config

from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config(
    "DEFAULT_MLXWHISPER_MODEL", default="mlx-community/whisper-large-v3-turbo"
)

# Decode options that mlx-whisper's transcribe() understands. Anything else
# (notably faster-whisper's vad_filter / vad_parameters) is dropped so the live
# pipeline can call this engine with the same kwargs it sends to faster-whisper.
_PASSTHROUGH = {
    "temperature",
    "condition_on_previous_text",
    "compression_ratio_threshold",
    "logprob_threshold",
    "no_speech_threshold",
    "prepend_punctuations",
    "append_punctuations",
    "clip_timestamps",
}


class MlxWhisperTranscriber:
    """Whisper on Apple Silicon via MLX (Metal GPU).

    MLX always targets the Apple Silicon GPU, so ``device`` is accepted for
    protocol compatibility but ignored. Configure the model repo via
    ``DEFAULT_MLXWHISPER_MODEL``.
    """

    def __init__(self, device: str = "mps", modelname: str | None = None):
        self.model_repo = modelname or DEFAULT_MODEL
        # Imported lazily so the entry point can register on non-Apple platforms
        # without mlx-whisper installed; it only fails if this engine is selected.
        import mlx_whisper

        self._mlx_whisper = mlx_whisper
        log.info(
            "MLX Whisper ready (repo=%s; device=%s ignored, MLX uses Metal)",
            self.model_repo, device,
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
        opts: dict = {
            "language": language,
            "task": task,
            "word_timestamps": word_timestamps,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt
        for key, value in kwargs.items():
            if key in _PASSTHROUGH:
                opts[key] = value

        result = self._mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.model_repo, **opts
        )

        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", language),
            segments=result.get("segments", []),
        )
