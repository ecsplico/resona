"""Error hierarchy for local TTS engines."""


class LocalTTSError(Exception):
    """Base class for all local TTS errors."""


class UnknownEngineError(LocalTTSError):
    """Requested an engine name that is not registered."""


class EngineUnavailableError(LocalTTSError):
    """The engine's native library is not installed.

    Raised (wrapping the original ImportError) when an engine is selected but
    its optional extra was never installed, e.g. ``pip install
    'resona-tts-local[kokoro]'``.
    """
