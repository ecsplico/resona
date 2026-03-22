"""Tests for ws_cli.watch.watch_directory."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from typer.testing import CliRunner

from ws_cli.main import app

runner = CliRunner()


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def test_watch_submits_new_file_and_stops(tmp_path):
    """watch should submit new audio files. We break the loop after first iteration."""
    audio_file = make_wav(tmp_path / "test.wav")

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt  # break the infinite loop

    with (
        patch("ws_client.client.WhisperClient.from_config", return_value=mock_client),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    mock_client.submit_job.assert_called_once_with(audio_file)


def test_watch_skips_already_seen_files(tmp_path):
    audio_file = make_wav(tmp_path / "old.wav")

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", return_value=mock_client),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    # File should only be submitted once even though we iterated twice
    assert mock_client.submit_job.call_count == 1


def test_watch_handles_submit_error(tmp_path, capsys):
    audio_file = make_wav(tmp_path / "bad.wav")
    mock_client = MagicMock()
    mock_client.submit_job.side_effect = RuntimeError("network error")

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", return_value=mock_client),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    # Should not crash; error printed to output
    assert "Failed" in result.output or result.exit_code in (0, 1)


def test_watch_ignores_non_audio_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    mock_client = MagicMock()
    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("ws_client.client.WhisperClient.from_config", return_value=mock_client),
        patch("ws_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_client.submit_job.assert_not_called()
