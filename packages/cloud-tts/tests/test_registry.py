"""Tests for the resona-cloud-tts registry."""
import pytest

from resona_cloud_tts.registry import (
    DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDERS,
    get_provider,
)


def test_providers_have_env_keys_and_models():
    for name in PROVIDERS:
        assert name in PROVIDER_ENV_KEYS
        assert name in DEFAULT_MODELS


def test_get_provider_returns_module_with_synthesize():
    mod = get_provider("openai")
    assert hasattr(mod, "synthesize")


def test_get_provider_rejects_unknown():
    with pytest.raises(ValueError):
        get_provider("nope")
