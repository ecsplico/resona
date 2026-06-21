"""Local TTS engine registry — names, metadata, lazy instantiation.

Mirrors ``resona_cloud_tts.registry`` but for in-process engines: each engine
is a stateful object (it caches a loaded model), so the registry memoizes one
instance per engine name.
"""
import importlib
from threading import Lock

from .errors import EngineUnavailableError, UnknownEngineError
from .protocol import LocalTTSEngine

# engine name -> (module, class)
_CLASSES: dict[str, tuple[str, str]] = {
    "kokoro": ("resona_tts_local.engines.kokoro", "KokoroEngine"),
    "chatterbox": ("resona_tts_local.engines.chatterbox", "ChatterboxEngine"),
    "chatterbox-turbo": (
        "resona_tts_local.engines.chatterbox_turbo",
        "ChatterboxTurboEngine",
    ),
    "qwen": ("resona_tts_local.engines.qwen", "QwenTTSEngine"),
    "qwen-custom-voice": (
        "resona_tts_local.engines.qwen_custom_voice",
        "QwenCustomVoiceEngine",
    ),
}

ENGINES: set[str] = set(_CLASSES)

# Human-facing capability metadata (languages, cloning support, preset voices).
ENGINE_INFO: dict[str, dict] = {
    "kokoro": {
        "display_name": "Kokoro-82M",
        "languages": ["en", "es", "fr", "hi", "it", "pt", "ja", "zh"],
        "cloning": False,
        "presets": True,
        "instruct": False,
        "sample_rate": 24000,
        "extra": "kokoro",
    },
    "chatterbox": {
        "display_name": "Chatterbox Multilingual",
        "languages": [
            "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it",
            "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh",
        ],
        "cloning": True,
        "presets": False,
        "instruct": False,
        "sample_rate": 24000,
        "extra": "chatterbox",
    },
    "chatterbox-turbo": {
        "display_name": "Chatterbox Turbo",
        "languages": ["en"],
        "cloning": True,
        "presets": False,
        "instruct": False,
        "paralinguistic": True,  # [laugh], [sigh], [cough] tags in text
        "sample_rate": 24000,
        "extra": "chatterbox",
    },
    "qwen": {
        "display_name": "Qwen3-TTS",
        "languages": ["zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"],
        "cloning": True,
        "presets": False,
        "instruct": True,
        "sample_rate": 24000,
        "extra": "qwen",
    },
    "qwen-custom-voice": {
        "display_name": "Qwen CustomVoice",
        "languages": ["zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"],
        "cloning": False,
        "presets": True,
        "instruct": True,
        "voices": [
            "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
            "Ryan", "Aiden", "Ono_Anna", "Sohee",
        ],
        "sample_rate": 24000,
        "extra": "qwen",
    },
}

_instances: dict[str, LocalTTSEngine] = {}
_lock = Lock()


def installed_engines() -> list[str]:
    """All registered engine names (registration is independent of native deps)."""
    return sorted(ENGINES)


def recommended_engine() -> str:
    """Best cross-platform default — Kokoro (tiny, CPU-realtime, Apache-2.0)."""
    return "kokoro"


def get_engine(name: str) -> LocalTTSEngine:
    """Return the singleton engine instance for ``name`` (lazy-instantiated).

    Raises:
        UnknownEngineError: ``name`` is not a registered engine.
        EngineUnavailableError: the engine's native library is not installed.
    """
    if name not in _CLASSES:
        raise UnknownEngineError(
            f"Unknown local TTS engine '{name}'. Known: {installed_engines()}"
        )
    if name not in _instances:
        with _lock:
            if name not in _instances:
                module, cls_name = _CLASSES[name]
                try:
                    cls = getattr(importlib.import_module(module), cls_name)
                except ImportError as exc:  # pragma: no cover - import wiring
                    raise EngineUnavailableError(str(exc)) from exc
                _instances[name] = cls()
    return _instances[name]


def reset() -> None:
    """Drop cached engine instances (frees loaded models; tests)."""
    _instances.clear()
