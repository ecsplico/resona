"""Tests for resona_cli.batch.batch_transcribe."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

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
    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])
    assert "No audio files found" in result.output
    mock_client.submit_job.assert_not_called()


def test_batch_submits_and_waits(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
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

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["batch", str(tmp_path)])

    assert mock_client.submit_job.call_count == 2
    assert mock_client.wait_for_job.call_count == 2


def test_batch_writes_output_files(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_client = make_client(transcript="transcribed text")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
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

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["batch", str(tmp_path), "--output-dir", str(out_dir)])

    txt_file = next((out_dir).glob("*.txt"))
    assert txt_file.read_text() == "md text"


def test_batch_handles_timeout(tmp_path):
    make_wav(tmp_path / "slow.wav")
    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}
    mock_client.wait_for_job.side_effect = TimeoutError("timed out")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert "Timeout" in result.output


def test_batch_handles_failed_job(tmp_path):
    make_wav(tmp_path / "fail.wav")
    mock_client = make_client(job_status="failed", transcript="")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert "failed" in result.output


# ── Fallback tests ────────────────────────────────────────────────────
import httpx


def _make_local_engine(transcript="Transcribed text"):
    """Mock LocalEngine context manager."""
    engine = MagicMock()
    engine.transcribe.return_value = {"text": transcript, "language": "de", "segments": []}
    # Make it work as a context manager
    engine.__enter__ = lambda s: engine
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def test_batch_fallback_used_when_no_server(tmp_path):
    """When from_config raises RuntimeError, LocalEngine is used instead."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()
    assert result.exit_code == 0


def test_batch_fallback_writes_text_to_audio_parent(tmp_path):
    """Fallback writes <stem>.txt next to the audio file when no --output-dir."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine(transcript="Hello world")

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        runner.invoke(app, ["batch", str(tmp_path)])

    txt = tmp_path / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Hello world"


def test_batch_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine(transcript="Output text")

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        runner.invoke(app, ["batch", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Output text"


def test_batch_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded to LocalEngine."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.batch.LocalEngine", return_value=mock_engine) as mock_le_cls,
    ):
        runner.invoke(app, ["batch", str(tmp_path), "--model", "large-v3", "--language", "en"])

    mock_le_cls.assert_called_once()
    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "large-v3"
    engine_transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert engine_transcribe_kwargs.get("language") == "en"


def test_batch_warns_when_model_flag_with_live_server(tmp_path):
    """--model should print a warning and be ignored when server is reachable."""
    make_wav(tmp_path / "audio.wav")
    mock_client = make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["batch", str(tmp_path), "--model", "large-v3"])

    assert "ignored" in result.output.lower() or "ignored" in (result.stderr or "").lower()
    mock_client.submit_job.assert_called_once()


def test_batch_fallback_continues_on_per_file_error(tmp_path):
    """A transcription error on one file does not abort processing of others."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_engine = _make_local_engine()
    mock_engine.transcribe.side_effect = [
        httpx.RequestError("connection refused"),
        {"text": "second file ok", "language": "de", "segments": []},
    ]

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.batch.LocalEngine", return_value=mock_engine),
    ):
        result = runner.invoke(app, ["batch", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0
