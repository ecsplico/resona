"""Tests for resona_cloud_stt — package skeleton, types, registry."""
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
