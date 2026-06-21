"""Apple MLX Whisper transcription backend for Resona.

Runs Whisper on the Apple Silicon GPU via the MLX framework. This is the
recommended engine on Mac: same model sizes as faster-whisper but it offloads
to the GPU instead of running on the CPU, typically a large speedup.

Models are HuggingFace repos of MLX-converted weights, e.g.
``mlx-community/whisper-large-v3-mlx``. A few friendly short names
(``large-v3``, ``medium`` …) are mapped to their ``mlx-community`` repos.
"""

import logging

import numpy as np
from decouple import config

from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

# Friendly short names -> mlx-community repos of pre-converted weights.
_MODEL_ALIASES = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}

DEFAULT_MODEL: str = config(
    "DEFAULT_MLX_WHISPER_MODEL", default="mlx-community/whisper-large-v3-mlx"
)


def _resolve_repo(name: str) -> str:
    return _MODEL_ALIASES.get(name, name)


class MLXWhisperTranscriber:
    """MLX-backed Whisper. GPU-accelerated on Apple Silicon.

    Configure model via ``DEFAULT_MLX_WHISPER_MODEL`` (a HuggingFace repo or one
    of the short aliases). The ``device`` argument is accepted for interface
    compatibility but ignored — MLX always uses the Apple GPU.
    """

    def __init__(self, device: str = "mps", modelname: str | None = None):
        self.model_repo = _resolve_repo(modelname or DEFAULT_MODEL)
        log.info("MLX Whisper backend ready (model repo: %s)", self.model_repo)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        vad_parameters: dict | None = None,
        **kwargs,
    ) -> TranscriptionResult:
        import mlx_whisper  # heavy/optional; imported lazily for fast startup

        # vad_filter/vad_parameters are faster-whisper-only knobs; mlx_whisper
        # has no VAD path and forwards unknown kwargs to DecodingOptions, so they
        # must be consumed here rather than passed through.

        # mlx_whisper expects a float32 waveform (or path); ensure the dtype.
        audio = np.asarray(audio, dtype=np.float32)
        opts = {
            "path_or_hf_repo": self.model_repo,
            "language": language,
            "task": task,
            "word_timestamps": word_timestamps,
            **kwargs,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        result = mlx_whisper.transcribe(audio, **opts)

        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", language),
            segments=result.get("segments", []),
        )
