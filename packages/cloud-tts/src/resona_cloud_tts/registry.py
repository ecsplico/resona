"""Provider registry — names, env keys, default models/voices, lookup."""
import importlib
from types import ModuleType

PROVIDERS: set[str] = {"openai", "elevenlabs", "deepgram"}

PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "tts-1",
    "elevenlabs": "eleven_multilingual_v2",
    "deepgram": "aura-2-thalia-en",
}

DEFAULT_VOICES: dict[str, str | None] = {
    "openai": "alloy",
    "elevenlabs": "21m00Tcm4TlvDq8ikWAM",  # ElevenLabs "Rachel" voice id
    "deepgram": None,                       # voice is encoded in the model
}

# MIME type for each supported response_format.
CONTENT_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
}


def get_provider(name: str) -> ModuleType:
    """Return the provider module for ``name`` (has a ``synthesize`` function).

    Raises:
        ValueError: if ``name`` is not a known provider.
    """
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Known: {sorted(PROVIDERS)}")
    return importlib.import_module(f"resona_cloud_tts.providers.{name}")
