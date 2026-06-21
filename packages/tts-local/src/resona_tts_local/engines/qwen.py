"""Qwen3-TTS local engine — multilingual zero-shot cloning + delivery instructions.

MLX-native on Apple Silicon (``mlx-audio``), PyTorch (``qwen-tts``) elsewhere —
mirroring Voicebox's ``mlx_backend.py`` / ``pytorch_backend.py`` split. Size is
selected via ``DEFAULT_QWEN_TTS_MODEL`` (``1.7b`` or ``0.6b``).
"""
import logging
import platform
import sys

import numpy as np
from decouple import config

from resona_asr_core.model_cache import configure_model_cache

from ..audio import to_numpy, wav_result
from ..types import SpeechResult
from ._base import lazy_import

log = logging.getLogger(__name__)

_MLX_REPOS = {
    "1.7b": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
    "0.6b": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
}
_PT_REPOS = {
    "1.7b": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "0.6b": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
}
DEFAULT_SIZE = config("DEFAULT_QWEN_TTS_MODEL", default="1.7b")
SAMPLE_RATE = 24000


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


class QwenTTSEngine:
    """Qwen3-TTS Base — zero-shot cloning, delivery instructions."""

    def __init__(self) -> None:
        configure_model_cache()
        self._model = None
        self._mlx = _is_apple_silicon()
        self._size = DEFAULT_SIZE.lower()

    def _repo(self) -> str:
        repos = _MLX_REPOS if self._mlx else _PT_REPOS
        return repos.get(self._size, repos["1.7b"])

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        if self._mlx:
            mlx_tts = lazy_import("mlx_audio.tts", install="uv pip install mlx-audio")
            log.info("Loading Qwen3-TTS (MLX) %s", self._repo())
            self._model = mlx_tts.load(self._repo())
        else:
            qwen = lazy_import("qwen_tts", install="uv pip install qwen-tts")
            log.info("Loading Qwen3-TTS (PyTorch) %s", self._repo())
            self._model = qwen.Qwen3TTSModel.from_pretrained(self._repo())

    def _generate_mlx(
        self, text: str, language: str, ref_audio: str | None, ref_text: str | None
    ) -> tuple[np.ndarray, int]:
        sr = SAMPLE_RATE
        chunks: list[np.ndarray] = []
        # mlx-audio's generate() signature varies; try cloning, fall back to plain.
        try:
            if ref_audio:
                results = self._model.generate(
                    text, ref_audio=ref_audio, ref_text=ref_text, lang_code=language
                )
            else:
                results = self._model.generate(text, lang_code=language)
        except TypeError:
            results = self._model.generate(text)
        for result in results:
            chunks.append(to_numpy(result.audio))
            sr = int(getattr(result, "sample_rate", sr))
        samples = (
            np.concatenate(chunks) if chunks else np.zeros(sr, dtype=np.float32)
        )
        return samples, sr

    def _generate_pt(
        self,
        text: str,
        language: str,
        ref_audio: str | None,
        ref_text: str | None,
        instruct: str | None,
    ) -> tuple[np.ndarray, int]:
        prompt = None
        if ref_audio:
            prompt = self._model.create_voice_clone_prompt(ref_audio, ref_text)
        wavs, sr = self._model.generate_voice_clone(
            text=text,
            voice_clone_prompt=prompt,
            language=language,
            instruct=instruct,
        )
        return to_numpy(wavs[0]), int(sr)

    def synthesize_array(
        self,
        text: str,
        *,
        language: str = "en",
        ref_audio: str | None = None,
        ref_text: str | None = None,
        instruct: str | None = None,
        voice: str | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        self._ensure_model()
        ref_audio = ref_audio or voice
        if self._mlx:
            return self._generate_mlx(text, language, ref_audio, ref_text)
        return self._generate_pt(text, language, ref_audio, ref_text, instruct)

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
