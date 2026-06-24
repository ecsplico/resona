"""Tests for resona_cli.speech.speak command."""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

runner = CliRunner()
FAKE_AUDIO = b"\xff\xfb\x90\x00" * 100


def _make_speech_client(audio=FAKE_AUDIO):
    c = MagicMock()
    c.create_speech.return_value = audio
    return c


def test_speech_writes_default_output(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = _make_speech_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["speech", "hello world"])

    assert result.exit_code == 0
    assert (tmp_path / "speech.mp3").exists()
    assert (tmp_path / "speech.mp3").read_bytes() == FAKE_AUDIO


def test_speech_writes_to_custom_output(tmp_path):
    from resona_cli.main import app
    out = tmp_path / "out.mp3"
    mock_client = _make_speech_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["speech", "hello", "--output", str(out)])

    assert result.exit_code == 0
    assert out.read_bytes() == FAKE_AUDIO


def test_speech_forwards_voice_engine_model(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = _make_speech_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["speech", "hi",
                             "--voice", "nova",
                             "--engine", "openai",
                             "--model", "tts-1-hd",
                             "--format", "opus",
                             "--speed", "1.2"])

    call_kwargs = mock_client.create_speech.call_args.kwargs
    assert call_kwargs.get("voice") == "nova"
    assert call_kwargs.get("engine") == "openai"
    assert call_kwargs.get("model") == "tts-1-hd"
    assert call_kwargs.get("response_format") == "opus"
    assert abs(call_kwargs.get("speed", 0) - 1.2) < 0.001


def test_speech_http_error_exits_nonzero(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = MagicMock()
    req = MagicMock()
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad engine"
    mock_client.create_speech.side_effect = httpx.HTTPStatusError(
        "bad", request=req, response=resp
    )

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["speech", "hi"])

    assert result.exit_code != 0
    assert "bad engine" in result.output


def _fake_local_engine(audio=b"RIFFlocalwav"):
    eng = MagicMock()
    eng.synthesize.return_value = {
        "audio": audio, "content_type": "audio/wav", "sample_rate": 22050,
    }
    return eng


def test_speech_no_server_falls_back_to_local(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    eng = _fake_local_engine()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_tts_local.registry.get_engine", return_value=eng),
        patch("resona_tts_local.registry.recommended_offline_engine",
              return_value="piper"),
    ):
        result = runner.invoke(app, ["speech", "Guten Tag"])

    assert result.exit_code == 0
    eng.synthesize.assert_called_once()
    assert eng.synthesize.call_args.kwargs.get("language") == "de"
    assert (tmp_path / "speech.wav").exists()


def test_speech_connect_error_falls_back_to_local(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = MagicMock()
    mock_client.create_speech.side_effect = httpx.ConnectError("refused")
    eng = _fake_local_engine()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_tts_local.registry.get_engine", return_value=eng),
        patch("resona_tts_local.registry.recommended_offline_engine",
              return_value="piper"),
    ):
        result = runner.invoke(app, ["speech", "hi"])

    assert result.exit_code == 0
    eng.synthesize.assert_called_once()


def test_speech_default_omits_voice(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = _make_speech_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["speech", "hi"])

    # No --voice given → don't force a voice; let the engine pick its default.
    assert "voice" not in mock_client.create_speech.call_args.kwargs


def test_speech_play_flag_calls_player(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = _make_speech_client()

    # Simulate only aplay available (first in spec order)
    def which_aplay_only(name):
        return "/usr/bin/aplay" if name == "aplay" else None

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("shutil.which", side_effect=which_aplay_only),
        patch("subprocess.run") as mock_run,
    ):
        result = runner.invoke(app, ["speech", "hi", "--play"])

    assert result.exit_code == 0
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "aplay"


def test_speech_play_no_player_warns(tmp_path, monkeypatch):
    from resona_cli.main import app
    monkeypatch.chdir(tmp_path)
    mock_client = _make_speech_client()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["speech", "hi", "--play"])

    assert result.exit_code == 0
    assert "no audio player" in result.output.lower()
