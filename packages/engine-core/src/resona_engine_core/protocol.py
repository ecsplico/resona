"""Back-compat shim — protocol now lives in resona-asr-core."""
from resona_asr_core.protocol import Transcriber, TranscriptionResult  # noqa: F401

__all__ = ["Transcriber", "TranscriptionResult"]
