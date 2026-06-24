"""Piper local TTS engine — torch-free (ONNX / onnxruntime).

The only local engine that does **not** pull torch: Piper runs Whisper-quality
neural voices on the CPU via onnxruntime, cross-platform. This makes it the
out-of-the-box offline fallback for ``resona speech`` (the default ``resona-cli``
install is deliberately torch-free).

Preset voices only (no cloning / instruct). Voices are downloaded on first use
into ``PIPER_VOICES_DIR`` (or ``<model_cache_dir()>/piper``).
"""
import logging
from pathlib import Path

import numpy as np
from decouple import config

from resona_asr_core.model_cache import configure_model_cache, model_cache_dir

from ..audio import wav_result
from ..errors import EngineUnavailableError
from ..types import SpeechResult
from ._base import lazy_import

log = logging.getLogger(__name__)

_INSTALL = "pip install piper-tts"

DEFAULT_LANGUAGE = "de"
DEFAULT_VOICE = "de_DE-thorsten-medium"

# ISO language code -> a known good Piper preset voice.
LANG_DEFAULT_VOICE = {
    "de": "de_DE-thorsten-medium",
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "fr": "fr_FR-siwis-medium",
    "it": "it_IT-paola-medium",
    "nl": "nl_NL-mls-medium",
    "ru": "ru_RU-irina-medium",
}


def _voices_dir() -> Path:
    """Directory holding downloaded ``<voice>.onnx`` files (created if absent)."""
    custom = config("PIPER_VOICES_DIR", default="")
    base = Path(custom).expanduser() if custom else Path(model_cache_dir()) / "piper"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve_voice(voice: str | None, language: str) -> str:
    """Map an explicit voice / language to a Piper voice id.

    Explicit Piper voice ids (containing ``-``, e.g. ``de_DE-thorsten-medium``)
    are used verbatim; otherwise fall back to the language default, then German.
    """
    if voice and "-" in voice:
        return voice
    return LANG_DEFAULT_VOICE.get(language, DEFAULT_VOICE)


class PiperEngine:
    """Piper (ONNX) — torch-free, preset voices, cross-platform."""

    def __init__(self) -> None:
        configure_model_cache()
        self._voices: dict[str, object] = {}  # voice id -> PiperVoice

    def _ensure_voice(self, voice_id: str):
        """Load (downloading if needed) and cache the ``PiperVoice`` for ``voice_id``."""
        if voice_id in self._voices:
            return self._voices[voice_id]

        piper = lazy_import("piper", install=_INSTALL)
        voices_dir = _voices_dir()
        onnx_path = voices_dir / f"{voice_id}.onnx"

        if not onnx_path.exists():
            try:
                from piper.download_voices import download_voice

                log.info("Downloading Piper voice %s -> %s", voice_id, voices_dir)
                download_voice(voice_id, voices_dir)
            except Exception as exc:  # noqa: BLE001 — offline / unknown voice
                raise EngineUnavailableError(
                    f"Piper voice '{voice_id}' is not available and could not be "
                    f"downloaded ({exc}). Download it manually with:\n"
                    f"  python -m piper.download_voices {voice_id} "
                    f"--download-dir {voices_dir}"
                ) from exc

        voice = piper.PiperVoice.load(str(onnx_path))
        self._voices[voice_id] = voice
        return voice

    def synthesize_array(
        self,
        text: str,
        *,
        language: str = DEFAULT_LANGUAGE,
        voice: str | None = None,
        speed: float = 1.0,
        seed: int | None = None,
        **_kwargs,
    ) -> tuple[np.ndarray, int]:
        piper = lazy_import("piper", install=_INSTALL)
        voice_obj = self._ensure_voice(_resolve_voice(voice, language))
        # Piper: larger length_scale = slower, so speed is its inverse.
        cfg = piper.SynthesisConfig(length_scale=1.0 / speed if speed else 1.0)

        chunks: list[np.ndarray] = []
        sample_rate = 22050
        for chunk in voice_obj.synthesize(text, syn_config=cfg):
            sample_rate = chunk.sample_rate
            samples = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            chunks.append(samples.astype(np.float32) / 32768.0)

        if not chunks:
            return np.zeros(sample_rate, dtype=np.float32), sample_rate
        return np.concatenate(chunks), sample_rate

    def synthesize(self, text: str, **kwargs) -> SpeechResult:
        # ref_audio/ref_text/instruct accepted for protocol compatibility and
        # ignored — Piper has preset voices only.
        samples, sr = self.synthesize_array(text, **kwargs)
        return wav_result(samples, sr)
