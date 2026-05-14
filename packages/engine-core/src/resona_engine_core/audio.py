"""Back-compat shim — audio loader now lives in resona-asr-core."""
from resona_asr_core.audio import load_audio, SAMPLE_RATE  # noqa: F401

__all__ = ["load_audio", "SAMPLE_RATE"]
