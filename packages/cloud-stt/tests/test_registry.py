"""Tests for resona_cloud_stt — package skeleton, types, registry."""
import pytest

from resona_cloud_stt.errors import (
    CloudSTTError,
    MissingAPIKeyError,
    ProviderHTTPError,
)
from resona_cloud_stt.registry import (
    DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDERS,
    get_provider,
)
from resona_cloud_stt.types import TranscriptionResult


def test_transcription_result_shape():
    result: TranscriptionResult = {
        "text": "hello",
        "language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    }
    assert result["text"] == "hello"
    assert result["language"] == "en"
    assert result["segments"][0]["start"] == 0.0


def test_missing_api_key_error_is_cloud_stt_error():
    err = MissingAPIKeyError("DEEPGRAM_API_KEY")
    assert isinstance(err, CloudSTTError)
    assert err.env_var == "DEEPGRAM_API_KEY"
    assert "DEEPGRAM_API_KEY" in str(err)


def test_provider_http_error_carries_status_and_body():
    err = ProviderHTTPError("deepgram", 401, "Unauthorized")
    assert isinstance(err, CloudSTTError)
    assert err.status_code == 401
    assert err.provider == "deepgram"
    assert "401" in str(err)
    assert "Unauthorized" in str(err)


def test_providers_set_has_three_known_providers():
    assert PROVIDERS == {"deepgram", "elevenlabs", "openai"}


def test_provider_env_keys():
    assert PROVIDER_ENV_KEYS["deepgram"] == "DEEPGRAM_API_KEY"
    assert PROVIDER_ENV_KEYS["elevenlabs"] == "ELEVENLABS_API_KEY"
    assert PROVIDER_ENV_KEYS["openai"] == "OPENAI_API_KEY"


def test_default_models():
    assert DEFAULT_MODELS["deepgram"] == "nova-3"
    assert DEFAULT_MODELS["elevenlabs"] == "scribe_v1"
    assert DEFAULT_MODELS["openai"] == "whisper-1"


@pytest.mark.xfail(reason="deepgram module added in Task 4", strict=True)
def test_get_provider_returns_module_with_transcribe():
    mod = get_provider("deepgram")
    assert hasattr(mod, "transcribe")


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nonsense")
