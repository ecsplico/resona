"""Provider registry — names, env keys, default models, dynamic lookup."""
import importlib
from types import ModuleType

PROVIDERS: set[str] = {"deepgram", "elevenlabs", "openai"}

PROVIDER_ENV_KEYS: dict[str, str] = {
    "deepgram": "DEEPGRAM_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}

DEFAULT_MODELS: dict[str, str] = {
    "deepgram": "nova-3",
    "elevenlabs": "scribe_v1",
    "openai": "whisper-1",
}


def get_provider(name: str) -> ModuleType:
    """Return the provider module for ``name`` (has a ``transcribe`` function).

    Raises:
        ValueError: if ``name`` is not a known provider.
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Known: {sorted(PROVIDERS)}"
        )
    return importlib.import_module(f"resona_cloud_stt.providers.{name}")
