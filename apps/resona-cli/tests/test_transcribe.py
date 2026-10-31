"""Tests for resona_cli.transcribe.transcribe_files."""
import io
import json
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import JobRef, JobTimeout, JobView, PolicyDeniedError
from resona_client.config import EngineConfig, EngineEntry
from resona_job_core import JobResult
from resona_postprocess.pipeline import PostprocessResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_local_engine_by_default(monkeypatch):
    """These tests exercise the InProcessEngine/LocalEngine fallback paths;
    assume no engine-server is already running on the detection port,
    regardless of the host machine's actual local state."""
    monkeypatch.setattr("resona_cli.transcribe.probe_local_engine", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _no_hub_by_default(monkeypatch):
    """Isolate tests from the host's RESONA_API_URL and ~/.resona/config.json."""
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))


@pytest.fixture
def hub(monkeypatch):
    """An explicitly configured hub (RESONA_API_URL)."""
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


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


def _job_view(job_id, status="completed", error=None):
    return JobView.from_dict({"job_id": job_id, "kind": "audio", "status": status,
                              "private": True, "error": error})


def _make_gateway_client(text="Hello world"):
    """A mocked ResonaClient whose audio jobs complete with *text* as Markdown."""
    client = MagicMock()
    ids = iter(range(1, 100))
    client.submit_job.side_effect = lambda *a, **k: JobRef(job_id=next(ids), status="pending")
    client.wait_for_job.side_effect = lambda job_id, **k: _job_view(job_id)
    client.get_result.return_value = JobResult(
        kind="audio", raw_text=text.lower(), markdown=text, language="de",
        provenance={"stages": [{"stage": "asr", "engine": "mlx-whisper",
                                "model": "whisper-large-v3", "accelerator": "metal",
                                "seconds": 1.0}]},
    )
    return client


def _noop_pipeline():
    """Return a mock pipeline whose .run() returns a PostprocessResult."""
    p = MagicMock()
    p.run.side_effect = lambda t: PostprocessResult(text=t, data={})
    return p


def _mock_resolve_and_pipeline(pipeline=None):
    """Return patchers for resolve_profile and build_pipeline."""
    if pipeline is None:
        pipeline = _noop_pipeline()
    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_build = MagicMock(return_value=pipeline)
    return mock_resolve, mock_build


# ── Gateway path ──────────────────────────────────────────────────────────────

def test_transcribe_no_files(tmp_path):
    result = runner.invoke(app, ["transcribe", str(tmp_path)])
    assert "No audio files found" in result.output


def test_transcribe_hub_submits_audio_job_for_single_file(tmp_path, hub):
    f = make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(f)])

    assert result.exit_code == 0
    mock_client.submit_job.assert_called_once()
    kwargs = mock_client.submit_job.call_args.kwargs
    assert kwargs["kind"] == "audio"
    assert kwargs["private"] is True
    mock_client.wait_for_job.assert_called_once_with(1)


def test_transcribe_hub_called_for_directory(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    assert mock_client.submit_job.call_count == 2


def test_transcribe_hub_writes_result_markdown(tmp_path, hub):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_client = _make_gateway_client(text="Transcribed text.")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    assert [p.name for p in out_dir.glob("*.md")] == ["audio.md"]
    assert read_md_body(out_dir / "audio.md") == "Transcribed text."
    frontmatter = (out_dir / "audio.md").read_text()
    assert 'engine: "mlx-whisper"' in frontmatter
    assert 'job_id: "1"' in frontmatter


def test_transcribe_hub_forwards_engine_model_language_private_profile(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper",
                            "--model", "large-v3", "--language", "en",
                            "--no-private", "--profile", "medical"])

    kwargs = mock_client.submit_job.call_args.kwargs
    assert kwargs["engine"] == "whisper"
    assert kwargs["model"] == "large-v3"
    assert kwargs["language"] == "en"
    assert kwargs["private"] is False
    assert kwargs["profile"] == "medical"


def test_transcribe_hub_from_config_entry(tmp_path, monkeypatch):
    """A resona-api entry in ~/.resona/config.json counts as an explicitly configured hub."""
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()
    cfg = EngineConfig(engines=[EngineEntry(name="hub", api_url="https://hub.test")])
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cfg))

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.transcribe.LocalEngine") as mock_le,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert result.exit_code == 0
    mock_client.submit_job.assert_called_once()
    mock_le.assert_not_called()


def test_transcribe_deduplicates_overlapping_inputs(tmp_path, hub):
    f = make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(f), str(tmp_path)])

    assert mock_client.submit_job.call_count == 1


def test_transcribe_glob_filters_non_audio(tmp_path, monkeypatch, hub):
    make_wav(tmp_path / "a.wav")
    (tmp_path / "b.txt").write_text("nope")
    monkeypatch.chdir(tmp_path)
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", "*"])

    assert mock_client.submit_job.call_count == 1


def test_transcribe_missing_path_warns(tmp_path, hub):
    mock_client = _make_gateway_client()
    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path / "nope.wav")])
    assert "Not found" in result.output
    mock_client.submit_job.assert_not_called()


def test_transcribe_hub_failed_job_reported_others_written(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = _make_gateway_client(text="ok")
    mock_client.wait_for_job.side_effect = lambda job_id, **k: (
        _job_view(job_id, "failed", {"code": "unsupported_media", "message": "cannot decode"})
        if job_id == 1 else _job_view(job_id)
    )

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert result.exit_code == 1
    assert "cannot decode" in result.output
    assert not (tmp_path / "a.md").exists()
    assert read_md_body(tmp_path / "b.md") == "ok"


def test_transcribe_hub_rejects_profile_file(tmp_path, hub):
    """Profile files/inline JSON are local-mode only; with a hub they are a clear error."""
    make_wav(tmp_path / "a.wav")
    profile_file = tmp_path / "myprofile.json"
    profile_file.write_text(json.dumps({"name": "test", "steps": []}), encoding="utf-8")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--profile", str(profile_file)])
        inline = runner.invoke(app, ["transcribe", str(tmp_path), "--profile", '{"name": "x"}'])

    assert result.exit_code == 1
    assert "local mode" in result.output
    assert inline.exit_code == 1
    mock_client.submit_job.assert_not_called()


def test_transcribe_hub_policy_denied_exits_2(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()
    mock_client.submit_job.side_effect = PolicyDeniedError(
        403, "policy_denied", "cloud processing is disabled for this organization")

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.transcribe.LocalEngine") as mock_le,
        patch("resona_cli.transcribe.InProcessEngine") as mock_ipe,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--no-private"])

    assert result.exit_code == 2
    assert "policy_denied: cloud processing is disabled" in result.output
    mock_le.assert_not_called()
    mock_ipe.assert_not_called()


def test_transcribe_hub_job_denied_by_policy_exits_2(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()
    mock_client.wait_for_job.side_effect = lambda job_id, **k: _job_view(
        job_id, "failed", {"code": "policy_denied", "message": "endpoint revoked"})

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert result.exit_code == 2
    assert "policy_denied: endpoint revoked" in result.output
    mock_client.get_result.assert_not_called()


# ── Fallback path (no gateway) ────────────────────────────────────────────────

def test_transcribe_fallback_used_when_no_server(tmp_path):
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()
    assert result.exit_code == 0


@pytest.mark.parametrize("error", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    JobTimeout(1),
])
def test_transcribe_hub_error_never_falls_back_to_local(tmp_path, hub, error):
    make_wav(tmp_path / "audio.wav")
    mock_client = _make_gateway_client()
    if isinstance(error, JobTimeout):
        mock_client.wait_for_job.side_effect = error
    else:
        mock_client.submit_job.side_effect = error

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.transcribe.InProcessEngine") as mock_ipe,
        patch("resona_cli.transcribe.LocalEngine") as mock_le,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert result.exit_code == 1
    assert "Error" in result.output
    mock_ipe.assert_not_called()
    mock_le.assert_not_called()
    assert not (tmp_path / "audio.md").exists()


def test_transcribe_unreachable_configured_hub_never_falls_back(tmp_path, hub):
    make_wav(tmp_path / "audio.wav")

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("No reachable resona engine found.")),
        patch("resona_cli.transcribe.InProcessEngine") as mock_ipe,
        patch("resona_cli.transcribe.LocalEngine") as mock_le,
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert result.exit_code == 1
    assert "unreachable" in result.output
    mock_ipe.assert_not_called()
    mock_le.assert_not_called()


def test_transcribe_fallback_writes_text_next_to_audio(tmp_path):
    make_wav(tmp_path / "speech.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "Hello world", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    assert read_md_body(tmp_path / "speech.md") == "Hello world"


def test_transcribe_fallback_respects_output_dir(tmp_path):
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "Output text", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert read_md_body(out_dir / "speech.md") == "Output text"


def test_transcribe_fallback_passes_model_and_language(tmp_path):
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path),
                             "--model", "large-v3", "--language", "en"])

    mock_le_cls.assert_called_once()
    assert mock_le_cls.call_args.kwargs.get("model") == "large-v3"
    assert mock_engine.transcribe.call_args.kwargs.get("language") == "en"


def test_transcribe_fallback_builtin_engine_forwarded(tmp_path):
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper"])

    assert mock_le_cls.call_args.kwargs.get("engine") == "whisper"


def test_transcribe_fallback_non_builtin_engine_uses_default(tmp_path):
    """When --engine names a cloud entry and no gateway, fall back to default local engine."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "x", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    from resona_client.config import EngineConfig
    mock_cfg = EngineConfig(engines=[], default_engine="voxtral")

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_client.config.EngineConfig.load", return_value=mock_cfg),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "deepgram"])

    assert mock_le_cls.call_args.kwargs.get("engine") == "voxtral"


def test_transcribe_fallback_applies_postprocess_pipeline(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "hello", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PostprocessResult(text="HELLO", data={})

    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_build = MagicMock(return_value=mock_pipeline)

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    mock_pipeline.run.assert_called_once_with("hello")
    assert read_md_body(out_dir / "audio.md") == "HELLO"


def test_transcribe_uses_in_process_engine_when_available(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path),
                                      "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()
    assert read_md_body(out_dir / "audio.md") == "hi"


def test_transcribe_fallback_private_flag_runs_local(tmp_path):
    """--private with no gateway still runs the local engine (local is inherently private)."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "local", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--private"])

    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()


def test_transcribe_fallback_writes_json_sidecar_when_data_nonempty(tmp_path):
    """When pipeline.run() returns non-empty data, a <stem>.json sidecar is written."""
    make_wav(tmp_path / "report.wav")
    out_dir = tmp_path / "out"
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "raw", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PostprocessResult(
        text="processed", data={"fields": {"x": 1}}
    )
    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_build = MagicMock(return_value=mock_pipeline)

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    md_path = out_dir / "report.md"
    sidecar_path = out_dir / "report.json"
    assert md_path.exists(), "transcript .md file should be written"
    assert read_md_body(md_path) == "processed"
    assert sidecar_path.exists(), "sidecar .json file should be written when data is non-empty"
    sidecar_data = json.loads(sidecar_path.read_text())
    assert sidecar_data == {"fields": {"x": 1}}


def test_transcribe_fallback_resolves_profile_name(tmp_path):
    """Local fallback resolves --profile NAME via resolve_profile and runs build_pipeline."""
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "profiled", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_profile = MagicMock()
    mock_resolve = MagicMock(return_value=mock_profile)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = PostprocessResult(text="profiled", data={})
    mock_build = MagicMock(return_value=mock_pipeline)

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path),
                             "--output-dir", str(out_dir), "--profile", "medical"])

    # resolve_profile called with our profile name
    mock_resolve.assert_called_once()
    call_args = mock_resolve.call_args
    assert call_args.args[0] == "medical"
    # build_pipeline called with the resolved profile and the explicit local policy
    mock_build.assert_called_once()
    assert mock_build.call_args.args[0] is mock_profile
    assert mock_build.call_args.kwargs["policy"] is not None
    assert read_md_body(out_dir / "audio.md") == "profiled"


def test_resolve_local_engine_name_explicit_builtin():
    """An explicit built-in --engine is used verbatim."""
    from resona_cli.transcribe import _resolve_local_engine_name
    assert _resolve_local_engine_name("whisper", "auto") == "whisper"


def test_resolve_local_engine_name_concrete_config_default():
    """A concrete default_engine from config pins the engine (no env detection)."""
    from resona_cli.transcribe import _resolve_local_engine_name
    assert _resolve_local_engine_name(None, "voxtral") == "voxtral"


def test_resolve_local_engine_name_auto_uses_recommended():
    """default_engine='auto' defers to the environment-aware recommendation."""
    from resona_cli import transcribe
    with patch("resona_asr_core.registry.recommended_engine", return_value="mlx-whisper") as rec:
        assert transcribe._resolve_local_engine_name(None, "auto") == "mlx-whisper"
        rec.assert_called_once()
