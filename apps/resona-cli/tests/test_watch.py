"""Tests for resona_cli.watch.watch_directory."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
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
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
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
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
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
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_client.submit_job.assert_not_called()


# ── Fallback tests ────────────────────────────────────────────────────
import httpx
from unittest.mock import MagicMock


def _make_local_engine_watch(transcript="Watched text"):
    engine = MagicMock()
    engine.transcribe.return_value = {"text": transcript, "language": "de", "segments": []}
    engine.__enter__ = lambda s: engine
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def test_watch_fallback_used_when_no_server(tmp_path):
    """When from_config raises RuntimeError, LocalEngine is used."""
    audio_file = make_wav(tmp_path / "test.wav")
    mock_engine = _make_local_engine_watch()

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()


def test_watch_fallback_writes_txt_next_to_audio(tmp_path):
    """Fallback writes <stem>.txt next to audio file."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine_watch(transcript="Watch result")

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    txt = tmp_path / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Watch result"


def test_watch_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine_watch(transcript="Output dir result")

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "speech.txt"
    assert txt.exists()


def test_watch_fallback_continues_on_per_file_error(tmp_path):
    """A transcription error does not abort the watch loop."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")

    call_count = 0
    mock_engine = _make_local_engine_watch()
    mock_engine.transcribe.side_effect = [
        httpx.RequestError("refused"),
        {"text": "ok", "language": "de", "segments": []},
    ]

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0


def test_watch_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded correctly."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine_watch()

    call_count = 0

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--model", "small", "--language", "fr"])

    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "small"
    transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert transcribe_kwargs.get("language") == "fr"
