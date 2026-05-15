"""Tests for resona_cloud_stt — package skeleton, types, registry."""
from resona_cloud_stt.errors import (
    CloudSTTError,
    MissingAPIKeyError,
    ProviderHTTPError,
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
