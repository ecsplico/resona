"""Tests for resona_cli.watch.watch_directory."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import JobRef, JobView, PolicyDeniedError
from resona_client.config import EngineConfig
from resona_job_core import JobResult
from resona_postprocess.pipeline import PostprocessResult

runner = CliRunner()


def read_md_body(path: Path) -> str:
    """Return the markdown body (after the YAML frontmatter), stripped."""
    text = path.read_text()
    assert text.startswith("---\n"), "expected YAML frontmatter"
    _, _, body = text[4:].partition("\n---\n")
    return body.strip()


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def _noop_pipeline():
    """Return a mock pipeline whose .run() returns a PostprocessResult."""
    p = MagicMock()
    p.run.side_effect = lambda t: PostprocessResult(text=t, data={})
    return p


def _mock_resolve_and_pipeline(pipeline=None):
    """Return mock resolve_profile and build_pipeline for watch tests."""
    if pipeline is None:
        pipeline = _noop_pipeline()
    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_build = MagicMock(return_value=pipeline)
    return mock_resolve, mock_build


@pytest.fixture(autouse=True)
def _no_hub_by_default(monkeypatch):
    """Isolate tests from the host's RESONA_API_URL and ~/.resona/config.json."""
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def _stop_after(n):
    """time.sleep replacement that breaks the watch loop after *n* scans."""
    calls = 0

    def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls >= n:
            raise KeyboardInterrupt
    return fake_sleep


def _make_hub_client(text="Watched by hub", status="completed", error=None):
    client = MagicMock()
    client.submit_job.return_value = JobRef(job_id=5, status="pending")
    client.wait_for_job.return_value = JobView.from_dict({
        "job_id": 5, "kind": "audio", "status": status, "private": True, "error": error,
    })
    client.get_result.return_value = JobResult(
        kind="audio", raw_text=text.lower(), markdown=text, language="de", provenance={},
    )
    return client


def test_watch_hub_writes_result_markdown(tmp_path, hub):
    audio_file = make_wav(tmp_path / "test.wav")
    mock_client = _make_hub_client()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(1)),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert mock_client.submit_job.call_args.args == (audio_file,)
    mock_client.wait_for_job.assert_called_once_with(5)
    assert read_md_body(tmp_path / "test.md") == "Watched by hub"
    assert "job 5" in result.output


def test_watch_skips_already_seen_files(tmp_path, hub):
    make_wav(tmp_path / "old.wav")
    mock_client = _make_hub_client()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(2)),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    # File should only be submitted once even though we iterated twice
    assert mock_client.submit_job.call_count == 1


def test_watch_hub_error_reported_without_local_fallback(tmp_path, hub):
    make_wav(tmp_path / "bad.wav")
    mock_client = _make_hub_client()
    mock_client.submit_job.side_effect = httpx.ConnectError("refused")

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.LocalEngine") as mock_le,
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(1)),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert "Failed to transcribe bad.wav" in result.output
    mock_le.assert_not_called()
    assert not (tmp_path / "bad.md").exists()


def test_watch_unreachable_hub_exits_without_local_fallback(tmp_path, hub):
    make_wav(tmp_path / "a.wav")

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("No reachable resona engine found.")),
        patch("resona_cli.watch.LocalEngine") as mock_le,
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert result.exit_code == 1
    mock_le.assert_not_called()


def test_watch_hub_policy_denied_exits_2(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_hub_client()
    mock_client.submit_job.side_effect = PolicyDeniedError(403, "policy_denied", "private required")

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(1)),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert result.exit_code == 2
    assert "policy_denied: private required" in result.output


def test_watch_ignores_non_audio_files(tmp_path, hub):
    (tmp_path / "notes.txt").write_text("not audio")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    mock_client = _make_hub_client()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(1)),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_client.submit_job.assert_not_called()


# ── Fallback tests ────────────────────────────────────────────────


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

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

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
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()


def test_watch_fallback_writes_md_next_to_audio(tmp_path):
    """Fallback writes <stem>.md next to audio file."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine_watch(transcript="Watch result")

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

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
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["watch", str(tmp_path)])

    md = tmp_path / "speech.md"
    assert md.exists()
    assert read_md_body(md) == "Watch result"


def test_watch_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine_watch(transcript="Output dir result")

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

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
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--output-dir", str(out_dir)])

    md = out_dir / "speech.md"
    assert md.exists()


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

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    def fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt

    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no server")),
        patch("resona_cli.watch.LocalEngine", return_value=mock_engine),
        patch("resona_cli.watch.time.sleep", side_effect=fake_sleep),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0


def test_watch_hub_submits_with_profile(tmp_path, hub):
    """Hub path submits a private audio job with profile= when --profile is given."""
    audio_file = make_wav(tmp_path / "test.wav")
    mock_client = _make_hub_client()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(1)),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--profile", "cardiology"])

    mock_client.submit_job.assert_called_once()
    args, kwargs = mock_client.submit_job.call_args
    assert args == (audio_file,)
    assert kwargs["kind"] == "audio"
    assert kwargs["profile"] == "cardiology"
    assert kwargs["private"] is True


def test_watch_fallback_writes_json_sidecar_when_data_nonempty(tmp_path):
    """When pipeline.run() returns non-empty data, a <stem>.json sidecar is written."""
    make_wav(tmp_path / "report.wav")
    mock_engine = _make_local_engine_watch(transcript="raw")

    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PostprocessResult(
        text="processed", data={"fields": {"x": 1}}
    )
    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_build = MagicMock(return_value=mock_pipeline)

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
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path)])

    import json
    assert result.exit_code == 0
    md_path = tmp_path / "report.md"
    sidecar_path = tmp_path / "report.json"
    assert md_path.exists(), "transcript .md file should be written"
    assert read_md_body(md_path) == "processed"
    assert sidecar_path.exists(), "sidecar .json file should be written when data is non-empty"
    sidecar_data = json.loads(sidecar_path.read_text())
    assert sidecar_data == {"fields": {"x": 1}}


def test_watch_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded correctly."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine_watch()

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

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
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["watch", str(tmp_path), "--model", "small", "--language", "fr"])

    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "small"
    transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert transcribe_kwargs.get("language") == "fr"
