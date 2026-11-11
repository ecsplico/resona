"""Speaker diarization for Resona workers.

``resona_diarize.turns`` is pure Python (word-level speaker assignment);
``resona_diarize.model`` imports torch/transformers and is loaded only to diarize.
"""

from .turns import Section, Turn, Word, assign_words, renumber, sections, speaker_at

ENGINE = "nemotron-diarization"
MODEL = "nvidia/Nemotron-3-Diarization"
# The files transformers loads. The repo also ships a NeMo checkpoint, a GGUF and a demo
# video (~300 MB) that the worker never reads, so prepare downloads only these.
WEIGHT_PATTERNS = ["*.json", "model.safetensors"]

__all__ = [
    "ENGINE",
    "MODEL",
    "WEIGHT_PATTERNS",
    "Section",
    "Turn",
    "Word",
    "assign_words",
    "renumber",
    "sections",
    "speaker_at",
]
