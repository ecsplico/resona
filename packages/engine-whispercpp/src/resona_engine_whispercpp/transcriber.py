"""whisper.cpp transcription backend for Resona (via pywhispercpp).

whisper.cpp runs the GGML Whisper models with hardware acceleration — Metal on
Apple Silicon, Accelerate/BLAS elsewhere. Lower memory than the PyTorch engines
and a strong speedup over CPU CTranslate2 on a Mac, at the same model size.

Models are GGML names that pywhispercpp downloads on demand, e.g. ``large-v3``,
``medium``, ``base.en``. Configure with ``DEFAULT_WHISPERCPP_MODEL``.
"""

import logging

import numpy as np
from decouple import config

from resona_asr_core.protocol import TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_WHISPERCPP_MODEL", default="large-v3")
# 0 -> let whisper.cpp pick (uses all hardware threads). Set to your performance
# core count on Apple Silicon (e.g. 12 on an M-series Max) for best throughput.
N_THREADS: int = config("WHISPERCPP_N_THREADS", default=0, cast=int)


class WhisperCppTranscriber:
    """whisper.cpp backend via pywhispercpp.

    The ``device`` argument is accepted for interface compatibility but ignored —
    whisper.cpp selects its accelerator (Metal/Accelerate) at build time.
    """

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        from pywhispercpp.model import Model  # heavy/optional; lazy import

        model_name = modelname or DEFAULT_MODEL
        kwargs = {"print_realtime": False, "print_progress": False}
        if N_THREADS > 0:
            kwargs["n_threads"] = N_THREADS
        log.info("Loading whisper.cpp model: %s (n_threads=%s)", model_name, N_THREADS or "auto")
        self.model = Model(model_name, **kwargs)

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
        # whisper.cpp wants a contiguous float32 mono waveform.
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        params = {
            "language": language,
            "translate": task == "translate",
        }
        if initial_prompt:
            params["initial_prompt"] = initial_prompt
        params.update(kwargs)

        segs = self.model.transcribe(audio, **params)

        segments = []
        for s in segs:
            # pywhispercpp timestamps are in 10ms units.
            segments.append(
                {
                    "start": getattr(s, "t0", 0) / 100.0,
                    "end": getattr(s, "t1", 0) / 100.0,
                    "text": s.text,
                }
            )
        text = "".join(seg["text"] for seg in segments)

        return TranscriptionResult(
            text=text,
            language=language,
            segments=segments,
        )
