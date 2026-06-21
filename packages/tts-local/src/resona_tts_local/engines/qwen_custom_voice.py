"""Qwen3-TTS CustomVoice local engine — preset speakers + instruct control.

No reference audio: pick one of 9 curated speakers and steer tone/emotion with a
natural-language ``instruct``. Uses the PyTorch ``qwen-tts`` library on every
platform (``generate_custom_voice``). Ported from Voicebox's
``qwen_custom_voice_backend.py``.
"""
import logging

import numpy as np
from decouple import config

from resona_asr_core.model_cache import configure_model_cache

from ..audio import to_numpy, wav_result
from ..types import SpeechResult
from ._base import lazy_import

log = logging.getLogger(__name__)

_PT_REPOS = {
    "1.7b": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "0.6b": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
}
PRESET_SPEAKERS = [
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
]
DEFAULT_SPEAKER = "Ryan"
DEFAULT_SIZE = config("DEFAULT_QWEN_TTS_MODEL", default="1.7b")
SAMPLE_RATE = 24000


class QwenCustomVoiceEngine:
    """Qwen3-TTS CustomVoice — 9 preset speakers with instruct control."""

    def __init__(self) -> None:
        configure_model_cache()
        self._model = None
        self._size = DEFAULT_SIZE.lower()

    def _repo(self) -> str:
        return _PT_REPOS.get(self._size, _PT_REPOS["1.7b"])

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        qwen = lazy_import("qwen_tts", install="uv pip install qwen-tts")
        log.info("Loading Qwen3-TTS CustomVoice %s", self._repo())
        self._model = qwen.Qwen3TTSModel.from_pretrained(self._repo())

    def synthesize_array(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str | None = None,
        instruct: str | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        self._ensure_model()
        speaker = voice or DEFAULT_SPEAKER
        kwargs = {"text": text, "speaker": speaker, "language": language}
        if instruct:
            kwargs["instruct"] = instruct
        wavs, sr = self._model.generate_custom_voice(**kwargs)
        return to_numpy(wavs[0]), int(sr)

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
