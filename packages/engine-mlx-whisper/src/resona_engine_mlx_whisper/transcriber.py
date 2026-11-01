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

from resona_asr_core.audio import SAMPLE_RATE
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


def _speech_chunks(audio: np.ndarray, vad_parameters: dict | None) -> list[dict]:
    """Silero VAD speech spans (sample offsets) — faster-whisper's bundled ONNX model, no download."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(audio, VadOptions(**(vad_parameters or {})), sampling_rate=SAMPLE_RATE)


def _restore_times(result: dict, chunks: list[dict]) -> list[dict]:
    """Map segment/word times from the concatenated speech back to original-audio time."""
    from faster_whisper.vad import SpeechTimestampsMap

    ts_map = SpeechTimestampsMap(chunks, SAMPLE_RATE)
    segments = []
    for segment in result.get("segments", []) or []:
        segment = dict(segment)
        words = segment.get("words")
        if words:
            restored = []
            for word in words:
                word = dict(word)
                index = ts_map.get_chunk_index((word["start"] + word["end"]) / 2)
                word["start"] = ts_map.get_original_time(word["start"], index)
                word["end"] = ts_map.get_original_time(word["end"], index)
                restored.append(word)
            segment["words"] = restored
            segment["start"], segment["end"] = restored[0]["start"], restored[-1]["end"]
        else:
            segment["start"] = ts_map.get_original_time(segment["start"])
            segment["end"] = ts_map.get_original_time(segment["end"])
        segments.append(segment)
    return segments


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

        # mlx_whisper has no VAD and forwards unknown kwargs to DecodingOptions, so
        # vad_filter/vad_parameters are consumed here. With vad_filter, only the
        # Silero-detected speech is decoded (like faster-whisper): no speech means an
        # empty transcript instead of Whisper's "Vielen Dank."-style hallucinations.

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

        if not vad_filter:
            result = mlx_whisper.transcribe(audio, **opts)
            return TranscriptionResult(
                text=result.get("text", ""),
                language=result.get("language", language),
                segments=result.get("segments", []),
            )

        chunks = _speech_chunks(audio, vad_parameters)
        if not chunks:
            return TranscriptionResult(text="", language=language, segments=[])
        speech = np.concatenate([audio[c["start"]:c["end"]] for c in chunks])
        result = mlx_whisper.transcribe(speech, **opts)
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", language),
            segments=_restore_times(result, chunks),
        )
