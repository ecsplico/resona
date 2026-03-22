"""Tests for ws_cli.batch.batch_transcribe."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def make_client(job_status="completed", transcript="Hello world"):
    client = MagicMock()
    client.submit_job.return_value = {"id": 1}
    client.wait_for_job.return_value = {"id": 1, "status": job_status, "transcript": transcript, "md": ""}
    return client


def test_batch_no_files(tmp_path):
    mock_client = MagicMock()
    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])
    assert "No audio files found" in result.output
    mock_client.submit_job.assert_not_called()


def test_batch_submits_and_waits(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = make_client()

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    mock_client.submit_job.assert_called_once()
    mock_client.wait_for_job.assert_called_once_with(1)
    assert "Completed" in result.output


def test_batch_multiple_files(tmp_path):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")

    mock_client = MagicMock()
    mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "x", "md": ""}

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        runner.invoke(app, ["batch", str(tmp_path)])

    assert mock_client.submit_job.call_count == 2
    assert mock_client.wait_for_job.call_count == 2


def test_batch_writes_output_files(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_client = make_client(transcript="transcribed text")

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        runner.invoke(app, ["batch", str(tmp_path), "--output-dir", str(out_dir)])

    txt_files = list(out_dir.glob("*.txt"))
    assert len(txt_files) == 1
    assert txt_files[0].read_text() == "transcribed text"


def test_batch_uses_md_when_transcript_empty(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "", "md": "md text"}

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        runner.invoke(app, ["batch", str(tmp_path), "--output-dir", str(out_dir)])

    txt_file = next((out_dir).glob("*.txt"))
    assert txt_file.read_text() == "md text"


def test_batch_handles_timeout(tmp_path):
    make_wav(tmp_path / "slow.wav")
    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}
    mock_client.wait_for_job.side_effect = TimeoutError("timed out")

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert "Timeout" in result.output


def test_batch_handles_failed_job(tmp_path):
    make_wav(tmp_path / "fail.wav")
    mock_client = make_client(job_status="failed", transcript="")

    with patch("ws_client.client.WhisperClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert "failed" in result.output
