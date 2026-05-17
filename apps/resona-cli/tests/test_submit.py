"""Tests for resona_cli.submit.submit_files."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

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


def _make_client(job_id=42):
    c = MagicMock()
    c.base_url = "http://localhost:7000"
    c.submit_job.return_value = {"id": job_id}
    return c


def test_submit_prints_url(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client(job_id=99)

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

    assert "http://localhost:7000/job/99" in result.output
    assert result.exit_code == 0


def test_submit_prints_one_url_per_file(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = MagicMock()
    mock_client.base_url = "http://localhost:7000"
    mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav"),
                                      str(tmp_path / "b.wav")])

    assert "http://localhost:7000/job/1" in result.output
    assert "http://localhost:7000/job/2" in result.output


def test_submit_forwards_engine(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--engine", "deepgram"])

    call_kwargs = mock_client.submit_job.call_args.kwargs
    assert call_kwargs.get("engine") == "deepgram"


def test_submit_forwards_translate(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--translate"])

    call_kwargs = mock_client.submit_job.call_args.kwargs
    assert call_kwargs.get("translate") is True


def test_submit_no_server_exits_with_error(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")

    with patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

    assert result.exit_code != 0


def test_submit_no_files_exits(tmp_path):
    from resona_cli.main import app
    with patch("resona_client.client.ResonaClient.from_config", return_value=_make_client()):
        result = runner.invoke(app, ["submit", str(tmp_path)])
    assert result.exit_code != 0
