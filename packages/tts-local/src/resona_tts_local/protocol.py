"""The contract every local TTS engine implements."""
from typing import Protocol, runtime_checkable

import numpy as np

from .types import SpeechResult


@runtime_checkable
class LocalTTSEngine(Protocol):
    """A local, in-process text-to-speech engine.

    Engines load their model lazily (on first ``synthesize``) so construction is
    cheap and the registry can list them without importing heavy native libs.
    """

    def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
        voice: str | None = None,
        ref_audio: str | None = None,
        ref_text: str | None = None,
        instruct: str | None = None,
        speed: float = 1.0,
        seed: int | None = None,
        **kwargs,
    ) -> SpeechResult:
        """Synthesize ``text`` to speech.

        Args:
            language: ISO language code (engine maps it to its own codes).
            voice: preset voice / speaker id (engines that have presets).
            ref_audio: path to reference audio for zero-shot voice cloning
                (engines that support cloning; ignored otherwise).
            ref_text: transcript of ``ref_audio`` when the engine needs it.
            instruct: natural-language delivery instruction ("speak slowly")
                for engines that support it (Qwen); ignored otherwise.
            speed: speaking-rate multiplier where supported.
            seed: RNG seed for reproducibility where supported.
        """
        ...

    def synthesize_array(
        self, text: str, **kwargs
    ) -> tuple[np.ndarray, int]:  # pragma: no cover - optional
        """Optional: return the raw ``(float32 waveform, sample_rate)``."""
        ...
