"""Tests for resona_cli.submit.submit_files."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_client.client import AudioOptions, JobRef, PolicyDeniedError


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
    c.submit_job.return_value = JobRef(job_id=job_id, status="pending")
    return c


def test_submit_prints_job_id(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client(job_id=99)

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

    assert result.exit_code == 0
    assert result.output.split() == ["99"]
    assert mock_client.submit_job.call_args.kwargs["kind"] == "audio"
    assert mock_client.submit_job.call_args.kwargs["private"] is True


def test_submit_prints_one_id_per_file(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = MagicMock()
    mock_client.submit_job.side_effect = [
        JobRef(job_id=1, status="pending"), JobRef(job_id=2, status="pending"),
    ]

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav"),
                                      str(tmp_path / "b.wav")])

    assert result.output.split() == ["1", "2"]


def test_submit_forwards_engine_and_profile(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--engine", "whisper",
                            "--profile", "medical"])

    call_kwargs = mock_client.submit_job.call_args.kwargs
    assert call_kwargs.get("engine") == "whisper"
    assert call_kwargs.get("profile") == "medical"


def test_submit_maps_translate_into_audio_options(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = _make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--translate"])

    assert mock_client.submit_job.call_args.kwargs["options"] == AudioOptions(translate=True)


def test_submit_policy_denied_exits_2(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    mock_client = MagicMock()
    mock_client.submit_job.side_effect = PolicyDeniedError(403, "policy_denied", "cloud disabled")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--no-private"])

    assert result.exit_code == 2
    assert "policy_denied: cloud disabled" in result.output


def test_submit_rejects_profile_file(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")
    prof = tmp_path / "p.json"
    prof.write_text('{"name": "x", "steps": []}')
    mock_client = _make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav"), "--profile", str(prof)])

    assert result.exit_code == 1
    assert "local mode" in result.output
    mock_client.submit_job.assert_not_called()


def test_submit_no_server_exits_with_error(tmp_path):
    from resona_cli.main import app
    make_wav(tmp_path / "a.wav")

    with patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")):
        result = runner.invoke(app, ["submit", str(tmp_path / "a.wav")])

    assert result.exit_code != 0
    assert "Error" in result.output


def test_submit_no_files_exits(tmp_path):
    from resona_cli.main import app
    with patch("resona_client.client.ResonaClient.from_config", return_value=_make_client()):
        result = runner.invoke(app, ["submit", str(tmp_path)])
    assert result.exit_code != 0
