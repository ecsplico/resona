"""Faster-whisper (CTranslate2) transcription backend for Resona."""

import logging

import numpy as np
from decouple import config
from faster_whisper import BatchedInferencePipeline, WhisperModel

from ._cuda_libs import preload_cuda_libs
from resona_asr_core.audio import SAMPLE_RATE
from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_FASTWHISPER_MODEL", default="large-v3")

# Tuning knobs (env-configurable). Defaults are chosen to maximise throughput on
# Apple Silicon / many-core CPUs without changing the model size.
#   FASTWHISPER_BEAM_SIZE   beam width; 1 = greedy (≈2-3x faster, tiny accuracy loss)
#   FASTWHISPER_CPU_THREADS intra-op threads; 0 lets CTranslate2 pick (use perf-core count)
#   FASTWHISPER_BATCHED     use BatchedInferencePipeline (VAD-chunked batching, big speedup)
#   FASTWHISPER_BATCH_SIZE  batch size for the batched pipeline
#   FASTWHISPER_COMPUTE_TYPE override compute type (e.g. int8, int8_float16, float16)
DEFAULT_BEAM_SIZE: int = config("FASTWHISPER_BEAM_SIZE", default=5, cast=int)
CPU_THREADS: int = config("FASTWHISPER_CPU_THREADS", default=0, cast=int)
USE_BATCHED: bool = config("FASTWHISPER_BATCHED", default=True, cast=bool)
BATCH_SIZE: int = config("FASTWHISPER_BATCH_SIZE", default=8, cast=int)

# The batched pipeline relies on Silero VAD to chunk audio. On audio whose
# acoustics the VAD misjudges it can silently discard large spans of speech,
# producing a transcript that covers only a fraction of the audio. When the
# batched segments cover less than MIN_COVERAGE of an audio clip at least
# MIN_COVERAGE_AUDIO_S long, we re-run on the sequential (non-VAD) path, which is
# slower but does not drop speech. Set FASTWHISPER_MIN_COVERAGE=0 to disable.
MIN_COVERAGE: float = config("FASTWHISPER_MIN_COVERAGE", default=0.5, cast=float)
MIN_COVERAGE_AUDIO_S: float = config("FASTWHISPER_MIN_COVERAGE_AUDIO_S", default=20.0, cast=float)

# CTranslate2 only supports these device strings. Anything else (notably "mps",
# which the registry returns when torch is installed alongside this engine) has
# no CTranslate2 backend and must fall back to CPU.
_CT2_DEVICES = {"cpu", "cuda", "auto"}


def _resolve_compute_type(device: str) -> str:
    explicit = config("FASTWHISPER_COMPUTE_TYPE", default="")
    if explicit:
        return explicit
    return "int8_float16" if device == "cuda" else "int8"


class FastWhisperTranscriber:
    """CTranslate2-based Whisper backend. Default and recommended.

    CTranslate2 has no Metal/MPS backend, so on Apple Silicon this runs on the
    CPU. Throughput is improved with a VAD-chunked batched pipeline and tunable
    beam size / thread count rather than GPU offload — for GPU acceleration on a
    Mac use the ``mlx-whisper``, ``whisper-cpp`` or ``lightning-mlx`` engines.
    """

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        if device not in _CT2_DEVICES:
            log.warning(
                "faster-whisper: device %r has no CTranslate2 backend; using 'cpu'. "
                "For GPU on Apple Silicon use the mlx-whisper / whisper-cpp engines.",
                device,
            )
            device = "cpu"
        compute_type = _resolve_compute_type(device)
        if device == "cuda":
            preload_cuda_libs()
        log.info(
            "Loading FastWhisper model: %s on %s (%s, cpu_threads=%s, batched=%s)",
            model_name, device, compute_type, CPU_THREADS, USE_BATCHED,
        )
        self.model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=CPU_THREADS,
        )
        self._pipeline = (
            BatchedInferencePipeline(model=self.model) if USE_BATCHED else None
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
        beam_size = kwargs.pop("beam_size", DEFAULT_BEAM_SIZE)
        condition_on_previous_text = kwargs.pop("condition_on_previous_text", False)
        opts = {
            "language": language,
            "task": task,
            "beam_size": beam_size,
            "condition_on_previous_text": condition_on_previous_text,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        # The batched pipeline does its own VAD chunking and does not support
        # word_timestamps; fall back to the sequential path when those are asked
        # for (or when batching is disabled).
        use_batched = self._pipeline is not None and not word_timestamps
        if use_batched:
            batched_opts = {**opts, "batch_size": BATCH_SIZE, **kwargs}
            segment_gen, info = self._pipeline.transcribe(audio, **batched_opts)
            segments = list(segment_gen)
            # Guard against the batched VAD silently dropping speech: if the
            # returned segments cover too little of the audio, re-run sequentially.
            if self._coverage_too_low(segments, audio):
                log.warning(
                    "faster-whisper: batched VAD covered only %.0f%% of %.0fs audio; "
                    "re-running on the sequential path to avoid dropped speech.",
                    100 * self._coverage(segments, audio), len(audio) / SAMPLE_RATE,
                )
                segments, info = self._sequential(audio, opts, kwargs, word_timestamps, vad_filter)
        else:
            segments, info = self._sequential(audio, opts, kwargs, word_timestamps, vad_filter)

        text = "".join(seg.text for seg in segments)
        return TranscriptionResult(
            text=text,
            language=info.language,
            segments=segments,
        )

    def _sequential(self, audio, opts, kwargs, word_timestamps, vad_filter):
        """Non-batched transcription. Robust (no VAD drop) but slower."""
        seq_opts = {**opts, "word_timestamps": word_timestamps, "vad_filter": vad_filter, **kwargs}
        segment_gen, info = self.model.transcribe(audio, **seq_opts)
        return list(segment_gen), info

    @staticmethod
    def _coverage(segments, audio: np.ndarray) -> float:
        """Fraction of the audio duration spanned by the transcribed segments."""
        duration = len(audio) / SAMPLE_RATE
        if duration <= 0:
            return 1.0
        covered = sum(max(0.0, seg.end - seg.start) for seg in segments)
        return min(1.0, covered / duration)

    @classmethod
    def _coverage_too_low(cls, segments, audio: np.ndarray) -> bool:
        if MIN_COVERAGE <= 0:
            return False
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_COVERAGE_AUDIO_S:
            return False
        return cls._coverage(segments, audio) < MIN_COVERAGE
