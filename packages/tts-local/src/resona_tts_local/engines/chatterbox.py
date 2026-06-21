"""Chatterbox Multilingual local TTS engine.

23 languages, zero-shot voice cloning from a reference sample. Forced to CPU on
Apple Silicon (no working MPS path). Ported from Voicebox's
``chatterbox_backend.py``.
"""
import logging

import numpy as np

from resona_asr_core.model_cache import configure_model_cache

from ..audio import to_numpy, wav_result
from ..types import SpeechResult
from ._base import lazy_import, seed_torch, torch_device

log = logging.getLogger(__name__)

HF_REPO = "ResembleAI/chatterbox"
DEFAULT_SAMPLE_RATE = 24000


class ChatterboxEngine:
    """Chatterbox Multilingual TTS. Zero-shot cloning via a reference clip."""

    def __init__(self) -> None:
        configure_model_cache()
        self._model = None
        self._device: str | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        mtl = lazy_import("chatterbox.mtl_tts", install="uv pip install chatterbox-tts")
        self._device = torch_device(force_cpu_on_mac=True)
        log.info("Loading Chatterbox Multilingual on %s", self._device)
        self._model = mtl.ChatterboxMultilingualTTS.from_pretrained(
            device=self._device
        )

    def synthesize_array(
        self,
        text: str,
        *,
        language: str = "en",
        ref_audio: str | None = None,
        voice: str | None = None,
        seed: int | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        self._ensure_model()
        seed_torch(seed)
        wav = self._model.generate(
            text,
            language_id=language,
            audio_prompt_path=ref_audio or voice,
        )
        sr = int(getattr(self._model, "sr", DEFAULT_SAMPLE_RATE))
        return to_numpy(wav), sr

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
