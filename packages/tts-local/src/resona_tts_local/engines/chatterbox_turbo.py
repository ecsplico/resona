"""Chatterbox Turbo local TTS engine.

Fast English-only 350M model with paralinguistic tags in the text — ``[laugh]``,
``[sigh]``, ``[cough]`` etc. Zero-shot cloning. Forced to CPU on Apple Silicon.
Ported from Voicebox's ``chatterbox_turbo_backend.py``.
"""
import logging

import numpy as np

from resona_asr_core.model_cache import configure_model_cache

from ..audio import to_numpy, wav_result
from ..types import SpeechResult
from ._base import lazy_import, seed_torch, torch_device

log = logging.getLogger(__name__)

HF_REPO = "ResembleAI/chatterbox-turbo"
DEFAULT_SAMPLE_RATE = 24000


class ChatterboxTurboEngine:
    """Chatterbox Turbo TTS. English, paralinguistic tags, zero-shot cloning."""

    def __init__(self) -> None:
        configure_model_cache()
        self._model = None
        self._device: str | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        turbo = lazy_import("chatterbox.tts_turbo", install="uv pip install chatterbox-tts")
        self._device = torch_device(force_cpu_on_mac=True)
        log.info("Loading Chatterbox Turbo on %s", self._device)
        self._model = turbo.ChatterboxTurboTTS.from_pretrained(device=self._device)

    def synthesize_array(
        self,
        text: str,
        *,
        ref_audio: str | None = None,
        voice: str | None = None,
        seed: int | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        self._ensure_model()
        seed_torch(seed)
        wav = self._model.generate(text, audio_prompt_path=ref_audio or voice)
        sr = int(getattr(self._model, "sr", DEFAULT_SAMPLE_RATE))
        return to_numpy(wav), sr

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        # language/instruct accepted for protocol compatibility, ignored (EN only).
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
