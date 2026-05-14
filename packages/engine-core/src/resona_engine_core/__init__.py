"""resona-engine-core — kept for backwards compatibility.

The lean contracts (protocol, registry, audio, live_transcriber) live in
`resona-asr-core`. This package now contains only the FastAPI HTTP/WS server.
Re-exports below let existing imports `from resona_engine_core import X` keep
working during the migration.
"""
from resona_asr_core.protocol import Transcriber, TranscriptionResult  # noqa: F401
from resona_asr_core.registry import get_transcriber, reset  # noqa: F401
from resona_asr_core.audio import load_audio, SAMPLE_RATE  # noqa: F401
from resona_asr_core.live_transcriber import LiveTranscriber  # noqa: F401
