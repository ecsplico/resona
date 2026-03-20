"""Tests for transcriber_factory singleton behaviour."""
import pytest
from unittest.mock import MagicMock, patch

import ws_engine.transcriber_factory as factory


@pytest.fixture(autouse=True)
def reset_singleton():
    """Isolate each test by resetting the module-level singleton."""
    factory._transcriber = None
    yield
    factory._transcriber = None


def test_singleton_returns_same_instance():
    mock_instance = MagicMock()
    with patch("ws_engine.transcriber_factory.FastWhisperTranscriber", return_value=mock_instance):
        t1 = factory.getTranscriber()
        t2 = factory.getTranscriber()
    assert t1 is t2


def test_singleton_constructs_only_once():
    mock_instance = MagicMock()
    with patch("ws_engine.transcriber_factory.FastWhisperTranscriber", return_value=mock_instance) as MockFW:
        factory.getTranscriber()
        factory.getTranscriber()
        factory.getTranscriber()
    assert MockFW.call_count == 1


def test_singleton_respects_asr_mode_whisper(monkeypatch):
    monkeypatch.setattr(factory, "MODE", "whisper")
    mock_instance = MagicMock()
    with patch("ws_engine.transcriber_factory.WhisperTranscriber", return_value=mock_instance) as MockW:
        t = factory.getTranscriber()
    assert t is mock_instance
    MockW.assert_called_once()


def test_singleton_respects_asr_mode_whisper_tf(monkeypatch):
    monkeypatch.setattr(factory, "MODE", "whisper-tf")
    mock_instance = MagicMock()
    with patch("ws_engine.transcriber_factory.TransformerTranscriber", return_value=mock_instance) as MockT:
        t = factory.getTranscriber()
    assert t is mock_instance
    MockT.assert_called_once()
