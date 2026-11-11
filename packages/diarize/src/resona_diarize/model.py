"""NVIDIA Nemotron-3-Diarization through transformers (``nemotron3_diarization``, >= 5.22.2).

The model scores up to eight speakers per 10 ms frame and numbers them in order of
first arrival. It runs offline over the whole recording on the device the job's ASR
candidate uses (``mps``, ``cuda`` or ``cpu``). Weights load only from a local
directory: the worker passes the pinned HuggingFace snapshot, nothing is downloaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .turns import Turn, renumber

if TYPE_CHECKING:
    import numpy as np

SAMPLE_RATE = 16000
# Frame probability above which a speaker counts as active (the processor's default).
THRESHOLD = 0.5


class Diarizer:
    """Loads the model once; call it with 16 kHz mono float32 audio."""

    def __init__(self, model_path: str, device: str) -> None:
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        self._torch = torch
        self.device = device
        self._processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        self._model = (
            AutoModelForAudioFrameClassification.from_pretrained(model_path, local_files_only=True)
            .to(device)
            .eval()
        )

    def __call__(self, audio: np.ndarray) -> list[Turn]:
        inputs = self._processor(audio, sampling_rate=SAMPLE_RATE).to(self._model.device, dtype=self._model.dtype)
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
        found = self._processor.extract_speaker_dict(logits, inputs.attention_mask, threshold=THRESHOLD)[0]
        return renumber(
            Turn(float(s["Start"]), float(s["End"]), int(s["Speaker"])) for s in found if s["End"] > s["Start"]
        )


__all__ = ["SAMPLE_RATE", "THRESHOLD", "Diarizer"]
