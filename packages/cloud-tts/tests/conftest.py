"""Shared fixtures for resona-cloud-tts tests."""
import pytest


@pytest.fixture
def fake_audio() -> bytes:
    """Stand-in for an encoded audio response body."""
    return b"ID3\x03\x00\x00\x00fake-mp3-bytes"
