"""Kokoro-82M local TTS engine.

Tiny (82M), Apache-2.0, CPU-realtime, cross-platform. Preset style voices (no
zero-shot cloning). Ported from Voicebox's ``kokoro_backend.py``.
"""
import logging

import numpy as np

from resona_asr_core.model_cache import configure_model_cache

from ..audio import to_numpy, wav_result
from ..types import SpeechResult
from ._base import lazy_import, seed_torch, torch_device

log = logging.getLogger(__name__)

HF_REPO = "hexgrad/Kokoro-82M"
SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"

# ISO language code -> Kokoro lang_code character.
LANG_CODE_MAP = {
    "en": "a",  # American English
    "es": "e",
    "fr": "f",
    "hi": "h",
    "it": "i",
    "pt": "p",
    "ja": "j",
    "zh": "z",
}


class KokoroEngine:
    """Kokoro-82M. Cross-platform, preset voices only."""

    def __init__(self) -> None:
        configure_model_cache()
        self._model = None
        self._pipelines: dict = {}  # kokoro lang_code -> KPipeline
        self._device: str | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        kokoro = lazy_import("kokoro", install="pip install 'resona-tts-local[kokoro]'")
        self._device = torch_device()
        log.info("Loading Kokoro-82M on %s", self._device)
        self._model = kokoro.KModel(repo_id=HF_REPO).to(self._device).eval()

    def _pipeline(self, language: str):
        code = LANG_CODE_MAP.get(language, "a")
        if code not in self._pipelines:
            kokoro = lazy_import("kokoro", install="pip install 'resona-tts-local[kokoro]'")
            self._pipelines[code] = kokoro.KPipeline(
                lang_code=code, repo_id=HF_REPO, model=self._model
            )
        return self._pipelines[code]

    def synthesize_array(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str | None = None,
        speed: float = 1.0,
        seed: int | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        self._ensure_model()
        seed_torch(seed)
        pipeline = self._pipeline(language)
        chunks: list[np.ndarray] = []
        for result in pipeline(text, voice=voice or DEFAULT_VOICE, speed=speed):
            audio = getattr(result, "audio", None)
            if audio is not None:
                chunks.append(to_numpy(audio))
        if not chunks:
            return np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE
        return np.concatenate(chunks).astype(np.float32), SAMPLE_RATE

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        # ref_audio/ref_text/instruct are accepted for protocol compatibility
        # and ignored — Kokoro uses preset voices.
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
