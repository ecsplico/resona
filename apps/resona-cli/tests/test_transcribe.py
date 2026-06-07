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


def _make_gateway_client(text="Hello world"):
    client = MagicMock()
    client.create_transcription.return_value = {
        "text": text, "language": "de", "segments": []
    }
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


def test_transcribe_gateway_called_for_single_file(tmp_path):
    f = make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(f)])

    mock_client.create_transcription.assert_called_once()
    assert result.exit_code == 0


def test_transcribe_gateway_called_for_directory(tmp_path):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    assert mock_client.create_transcription.call_count == 2


def test_transcribe_gateway_writes_output_files(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_client = _make_gateway_client(text="transcribed text")

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) == 1
    assert read_md_body(md_files[0]) == "transcribed text"


def test_transcribe_gateway_forwards_engine_flag(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "deepgram"])

    call_kwargs = mock_client.create_transcription.call_args.kwargs
    assert call_kwargs.get("engine") == "deepgram"


def test_transcribe_gateway_forwards_private_flag(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--private"])

    call_kwargs = mock_client.create_transcription.call_args.kwargs
    assert call_kwargs.get("private") is True


def test_transcribe_gateway_forwards_model_flag(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--model", "large-v3"])

    call_kwargs = mock_client.create_transcription.call_args.kwargs
    assert call_kwargs.get("model") == "large-v3"


def test_transcribe_deduplicates_overlapping_inputs(tmp_path):
    f = make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(f), str(tmp_path)])

    assert mock_client.create_transcription.call_count == 1


def test_transcribe_glob_filters_non_audio(tmp_path, monkeypatch):
    make_wav(tmp_path / "a.wav")
    (tmp_path / "b.txt").write_text("nope")
    monkeypatch.chdir(tmp_path)
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", "*"])

    assert mock_client.create_transcription.call_count == 1


def test_transcribe_missing_path_warns(tmp_path):
    mock_client = _make_gateway_client()
    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path / "nope.wav")])
    assert "Not found" in result.output
    mock_client.create_transcription.assert_not_called()


def test_transcribe_gateway_http_error_per_file_continues(tmp_path):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_client = MagicMock()
    mock_client.create_transcription.side_effect = [
        httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock()),
        {"text": "ok", "language": "de", "segments": []},
    ]

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert mock_client.create_transcription.call_count == 2
    assert result.exit_code == 0


def test_transcribe_gateway_forwards_profile_name(tmp_path):
    """--profile NAME is forwarded to create_transcription as profile='NAME'."""
    make_wav(tmp_path / "a.wav")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--profile", "medical"])

    call_kwargs = mock_client.create_transcription.call_args.kwargs
    assert call_kwargs.get("profile") == "medical"


def test_transcribe_gateway_forwards_profile_path_as_json(tmp_path):
    """--profile <path-to-json> reads the file and forwards its contents."""
    make_wav(tmp_path / "a.wav")
    profile_data = {"name": "test", "steps": []}
    profile_file = tmp_path / "myprofile.json"
    profile_file.write_text(json.dumps(profile_data), encoding="utf-8")
    mock_client = _make_gateway_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        runner.invoke(app, ["transcribe", str(tmp_path), "--profile", str(profile_file)])

    call_kwargs = mock_client.create_transcription.call_args.kwargs
    forwarded = call_kwargs.get("profile")
    assert forwarded is not None
    parsed = json.loads(forwarded)
    assert parsed["name"] == "test"


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


def test_transcribe_fallback_on_connect_error(tmp_path):
    make_wav(tmp_path / "audio.wav")
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "local", "language": "de", "segments": []}
    mock_engine.__enter__ = lambda s: mock_engine
    mock_engine.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.create_transcription.side_effect = httpx.ConnectError("refused")

    mock_resolve, mock_build = _mock_resolve_and_pipeline()

    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=mock_client),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.profile.resolve_profile", mock_resolve),
        patch("resona_postprocess.pipeline.build_pipeline", mock_build),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()
    assert result.exit_code == 0


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
    # build_pipeline called with the resolved profile
    mock_build.assert_called_once_with(mock_profile)
    assert read_md_body(out_dir / "audio.md") == "profiled"
