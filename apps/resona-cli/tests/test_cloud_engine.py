"""Tests for resona_cli.engine.CloudEngine."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from resona_client.config import EngineEntry
from resona_cli.engine import CloudEngine
from resona_cloud_stt.errors import MissingAPIKeyError


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def test_cloud_engine_resolves_key_and_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "secret-key")
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="dg", type="cloud", provider="deepgram",
                        model="nova-3", options={"smart_format": True})
    engine = CloudEngine(entry)

    fake_result = {"text": "hi", "language": "de", "segments": []}
    with patch("resona_cloud_stt.providers.deepgram.transcribe",
               return_value=fake_result) as mock_tx:
        result = engine.transcribe(audio, language="de")

    assert result == fake_result
    _, kwargs = mock_tx.call_args
    assert kwargs["api_key"] == "secret-key"
    assert kwargs["model"] == "nova-3"
    assert kwargs["language"] == "de"
    assert kwargs["options"] == {"smart_format": True}


def test_cloud_engine_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="dg", type="cloud", provider="deepgram")
    engine = CloudEngine(entry)
    with pytest.raises(MissingAPIKeyError, match="DEEPGRAM_API_KEY"):
        engine.transcribe(audio)


def test_cloud_engine_model_kwarg_overrides_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    audio = make_wav(tmp_path / "a.wav")
    entry = EngineEntry(name="oa", type="cloud", provider="openai", model="whisper-1")
    engine = CloudEngine(entry)
    with patch("resona_cloud_stt.providers.openai.transcribe",
               return_value={"text": "", "language": "", "segments": []}) as mock_tx:
        engine.transcribe(audio, model="gpt-4o-transcribe")
    assert mock_tx.call_args.kwargs["model"] == "gpt-4o-transcribe"
