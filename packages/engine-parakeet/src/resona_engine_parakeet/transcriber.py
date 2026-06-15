"""NVIDIA Parakeet (NeMo) transcription backend for Resona.

Parakeet is NVIDIA's FastConformer family of ASR models, run through the NeMo
toolkit. The ``parakeet-tdt-0.6b-v3`` default is multilingual (German + English
among 25 European languages) and very fast on CUDA; it also runs on CPU.

Two paths are provided:

* :meth:`ParakeetTranscriber.transcribe` — batch decode of a whole clip. This is
  all most callers need; ``resona live`` already drives it incrementally through
  the framework's windowed local-agreement path (same as the other engines).
* A native, low-latency cache-aware :class:`ParakeetStreamSession`, exposed only
  when ``RESONA_PARAKEET_STREAMING`` is enabled **and** the loaded model supports
  NeMo cache-aware streaming. It is opt-in so the proven fallback stays the
  default; see the README for the hardware/model requirements.
"""

import logging

import numpy as np
from decouple import config

from resona_asr_core.protocol import StreamUpdate, TranscriptionResult

log = logging.getLogger(__name__)

DEFAULT_MODEL: str = config("DEFAULT_PARAKEET_MODEL", default="nvidia/parakeet-tdt-0.6b-v3")

# Native cache-aware streaming is opt-in: it needs a cache-aware streaming model
# and is validated on GPU hardware. When off, the engine is a plain batch
# Transcriber and `resona live` uses the windowed local-agreement fallback.
STREAMING_ENABLED: bool = config("RESONA_PARAKEET_STREAMING", default=False, cast=bool)


def _text_delta(previous: str, current: str) -> str:
    """Return the newly-appended tail of ``current`` relative to ``previous``.

    Cache-aware streaming yields a cumulative transcript each step; the live
    layer wants only the *new* text. When ``current`` extends ``previous`` we
    return the suffix; otherwise (a revision) we return the whole ``current`` so
    no words are lost.
    """
    previous = (previous or "").strip()
    current = (current or "").strip()
    if not current:
        return ""
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous):].strip()
    return current


class ParakeetStreamSession:
    """Native cache-aware streaming session for a single live audio stream.

    Experimental and opt-in (``RESONA_PARAKEET_STREAMING``). Audio is fed as mono
    float32 at 16 kHz; each :meth:`feed` returns the new confirmed text since the
    last call. Errors degrade to an empty update so a live session never crashes.
    """

    def __init__(self, model, *, language: str = "de", task: str = "transcribe"):
        self.language = language
        self.task = task
        self._model = model
        self._committed = ""
        # Lazily created NeMo cache-aware buffer (its import lives in the engine pkg).
        from nemo.collections.asr.parts.utils.streaming_utils import (
            CacheAwareStreamingAudioBuffer,
        )

        self._buffer = CacheAwareStreamingAudioBuffer(model=model)
        self._cache = model.encoder.get_initial_cache_state(batch_size=1)
        self._prev_hyps = None

    def _decode(self, *, last_chunk: bool) -> str:
        """Drive NeMo cache-aware steps over whatever is buffered; return cumulative text."""
        text = self._committed
        cache_last_channel, cache_last_time, cache_last_channel_len = self._cache
        for chunk, chunk_len in self._buffer:
            (
                _pred,
                transcribed,
                cache_last_channel,
                cache_last_time,
                cache_last_channel_len,
                self._prev_hyps,
            ) = self._model.conformer_stream_step(
                processed_signal=chunk,
                processed_signal_length=chunk_len,
                cache_last_channel=cache_last_channel,
                cache_last_time=cache_last_time,
                cache_last_channel_len=cache_last_channel_len,
                keep_all_outputs=last_chunk,
                previous_hypotheses=self._prev_hyps,
                previous_pred_out=None,
                drop_extra_pre_encoded=None,
                return_transcription=True,
            )
            if transcribed:
                hyp = transcribed[0]
                text = hyp.text if hasattr(hyp, "text") else str(hyp)
        self._cache = (cache_last_channel, cache_last_time, cache_last_channel_len)
        return text

    def feed(self, audio: np.ndarray) -> "StreamUpdate | None":
        try:
            self._buffer.append_audio(np.asarray(audio, dtype=np.float32), stream_id=0)
            text = self._decode(last_chunk=False)
        except Exception as e:  # pragma: no cover - hardware/model specific
            log.error("Parakeet stream feed failed: %s", e, exc_info=True)
            return None
        delta = _text_delta(self._committed, text)
        if not delta:
            return None
        self._committed = text
        return StreamUpdate(confirmed_delta=delta, partial="", language=self.language)

    def flush(self) -> StreamUpdate:
        try:
            text = self._decode(last_chunk=True)
        except Exception as e:  # pragma: no cover - hardware/model specific
            log.error("Parakeet stream flush failed: %s", e, exc_info=True)
            return StreamUpdate(confirmed_delta="", partial="", language=self.language)
        delta = _text_delta(self._committed, text)
        self._committed = text
        return StreamUpdate(confirmed_delta=delta, partial="", language=self.language)


class ParakeetTranscriber:
    """NeMo Parakeet/FastConformer backend (CUDA-first, CPU-capable)."""

    def __init__(self, device: str = "cpu", modelname: str | None = None):
        self.model_name = modelname or DEFAULT_MODEL
        self.device = device
        # Imported lazily: nemo_toolkit is a heavy dep and the entry point should
        # register without it installed; it only fails if this engine is selected.
        from nemo.collections.asr.models import ASRModel

        log.info("Loading Parakeet model %s on %s", self.model_name, device)
        self._model = ASRModel.from_pretrained(model_name=self.model_name, map_location=device)
        self._model.eval()
        self._streaming = STREAMING_ENABLED and self._supports_streaming()
        if STREAMING_ENABLED and not self._streaming:
            log.warning(
                "RESONA_PARAKEET_STREAMING is set but %s is not a cache-aware "
                "streaming model; using batch decode.", self.model_name,
            )
        if self._streaming:
            # Bind the method only when usable so isinstance(StreamingTranscriber)
            # stays False otherwise and the live layer keeps the windowed fallback.
            self.stream_session = self._stream_session
        log.info("Parakeet ready (streaming=%s)", self._streaming)

    def _supports_streaming(self) -> bool:
        encoder = getattr(self._model, "encoder", None)
        return hasattr(self._model, "conformer_stream_step") and hasattr(
            encoder, "get_initial_cache_state"
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
        # NeMo accepts a list of float32 mono 16 kHz arrays and returns one
        # hypothesis per input. language/task/initial_prompt/vad_* are not part
        # of the NeMo decode API (the multilingual model auto-detects language),
        # so they are accepted for protocol compatibility and ignored.
        outputs = self._model.transcribe([np.asarray(audio, dtype=np.float32)], verbose=False)
        text = _hypothesis_text(outputs)
        return TranscriptionResult(text=text, language=language, segments=[])

    def _stream_session(
        self, *, language: str = "de", task: str = "transcribe"
    ) -> ParakeetStreamSession:
        return ParakeetStreamSession(self._model, language=language, task=task)


def _hypothesis_text(outputs) -> str:
    """Extract text from NeMo transcribe() output across return shapes.

    NeMo has returned, across versions: a list[str]; a list[Hypothesis] (with a
    ``.text`` attribute); or a tuple ``(best, all_hyps)``. Normalize to the first
    hypothesis's text.
    """
    if outputs is None:
        return ""
    if isinstance(outputs, tuple):
        outputs = outputs[0]
    if not outputs:
        return ""
    first = outputs[0]
    if hasattr(first, "text"):
        return (first.text or "").strip()
    return str(first).strip()
