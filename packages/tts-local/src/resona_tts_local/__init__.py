"""Local / offline text-to-speech engines for Resona.

Ported from Voicebox's local TTS backends into Resona's plugin style. Engines
run in-process (no engine-server) and are selected by name through
:mod:`resona_tts_local.registry`.

    from resona_tts_local.registry import get_engine
    result = get_engine("kokoro").synthesize("Hallo Welt", language="de")
    open("out.wav", "wb").write(result["audio"])
"""

from .errors import EngineUnavailableError, LocalTTSError, UnknownEngineError
from .registry import (
    ENGINE_INFO,
    ENGINES,
    get_engine,
    installed_engines,
    recommended_engine,
    recommended_offline_engine,
)
from .types import SpeechResult

__all__ = [
    "ENGINE_INFO",
    "ENGINES",
    "EngineUnavailableError",
    "LocalTTSError",
    "SpeechResult",
    "UnknownEngineError",
    "get_engine",
    "installed_engines",
    "recommended_engine",
    "recommended_offline_engine",
]
