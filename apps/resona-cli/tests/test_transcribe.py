"""Tests for resona_cli.transcribe.transcribe_files."""
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


def _make_resona_api_entry(name="srv", api_url="http://srv:7000", api_key=""):
    """Return a minimal EngineEntry-like mock for a resona-api engine."""
    from resona_client.config import EngineEntry
    return EngineEntry(name=name, api_url=api_url, api_key=api_key, type="resona-api")


def test_transcribe_no_files(tmp_path):
    result = runner.invoke(app, ["transcribe", str(tmp_path)])
    assert "No audio files found" in result.output


def test_transcribe_directory_submits_and_waits(tmp_path):
    make_wav(tmp_path / "a.wav")
    mock_client = make_client()
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    mock_client.submit_job.assert_called_once()
    mock_client.wait_for_job.assert_called_once_with(1)
    assert "Completed" in result.output


def test_transcribe_single_file(tmp_path):
    """Pass a single file path directly."""
    f = make_wav(tmp_path / "only.wav")
    mock_client = make_client()
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(f)])

    mock_client.submit_job.assert_called_once()
    called_path = mock_client.submit_job.call_args.args[0]
    assert Path(called_path).name == "only.wav"
    assert "Completed" in result.output


def test_transcribe_multiple_files_as_args(tmp_path):
    """Pass several file paths as separate arguments (shell-expanded glob equivalent)."""
    a = make_wav(tmp_path / "a.wav")
    b = make_wav(tmp_path / "b.wav")

    mock_client = MagicMock()
    mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "x", "md": ""}
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", str(a), str(b)])

    assert mock_client.submit_job.call_count == 2
    assert mock_client.wait_for_job.call_count == 2


def test_transcribe_quoted_glob_pattern(tmp_path, monkeypatch):
    """Pass a quoted glob pattern (`folder/*.mp3`) — expanded by Python, not the shell."""
    make_wav(tmp_path / "one.wav")
    make_wav(tmp_path / "two.wav")
    (tmp_path / "ignore.txt").write_text("not audio")

    monkeypatch.chdir(tmp_path)
    mock_client = MagicMock()
    mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "x", "md": ""}
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", "*.wav"])

    assert mock_client.submit_job.call_count == 2


def test_transcribe_glob_filters_non_audio(tmp_path, monkeypatch):
    """Glob expansion only includes audio extensions."""
    make_wav(tmp_path / "a.wav")
    (tmp_path / "b.txt").write_text("nope")
    (tmp_path / "c.json").write_text("{}")

    monkeypatch.chdir(tmp_path)
    mock_client = make_client()
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", "*"])

    assert mock_client.submit_job.call_count == 1


def test_transcribe_directory_with_multiple_files(tmp_path):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")

    mock_client = MagicMock()
    mock_client.submit_job.side_effect = [{"id": 1}, {"id": 2}]
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "x", "md": ""}
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    assert mock_client.submit_job.call_count == 2
    assert mock_client.wait_for_job.call_count == 2


def test_transcribe_deduplicates_overlapping_inputs(tmp_path):
    """Same file referenced via direct path and glob is only submitted once."""
    f = make_wav(tmp_path / "a.wav")
    mock_client = make_client()
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", str(f), str(tmp_path)])

    assert mock_client.submit_job.call_count == 1


def test_transcribe_writes_output_files(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_client = make_client(transcript="transcribed text")
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    txt_files = list(out_dir.glob("*.txt"))
    assert len(txt_files) == 1
    assert txt_files[0].read_text() == "transcribed text"


def test_transcribe_uses_md_when_transcript_empty(tmp_path):
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}
    mock_client.wait_for_job.return_value = {"id": 1, "status": "completed", "transcript": "", "md": "md text"}
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    txt_file = next((out_dir).glob("*.txt"))
    assert txt_file.read_text() == "md text"


def test_transcribe_handles_timeout(tmp_path):
    make_wav(tmp_path / "slow.wav")
    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 1}
    mock_client.wait_for_job.side_effect = TimeoutError("timed out")
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert "Timeout" in result.output


def test_transcribe_handles_failed_job(tmp_path):
    make_wav(tmp_path / "fail.wav")
    mock_client = make_client(job_status="failed", transcript="")
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert "failed" in result.output


def test_transcribe_missing_path_warns(tmp_path):
    """A path that does not exist prints a 'Not found' warning and is skipped."""
    mock_client = MagicMock()
    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["transcribe", str(tmp_path / "nope.wav")])

    assert "Not found" in result.output
    mock_client.submit_job.assert_not_called()


# ── Fallback tests ────────────────────────────────────────────────────
import httpx


def _make_local_engine(transcript="Transcribed text"):
    """Mock LocalEngine context manager."""
    engine = MagicMock()
    engine.transcribe.return_value = {"text": transcript, "language": "de", "segments": []}
    engine.__enter__ = lambda s: engine
    engine.__exit__ = MagicMock(return_value=False)
    return engine


def _noop_pipeline():
    """Mock pipeline that passes text through unchanged."""
    p = MagicMock()
    p.run.side_effect = lambda t: t
    return p


def test_transcribe_fallback_used_when_no_server(tmp_path):
    """When no engine resolves, LocalEngine is used instead."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    mock_engine.transcribe.assert_called_once()
    assert result.exit_code == 0


def test_transcribe_fallback_single_file(tmp_path):
    """Fallback works on a single file argument."""
    f = make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine(transcript="hi")

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(f)])

    assert (tmp_path / "speech.txt").read_text() == "hi"


def test_transcribe_fallback_writes_text_to_audio_parent(tmp_path):
    """Fallback writes <stem>.txt next to the audio file when no --output-dir."""
    make_wav(tmp_path / "speech.wav")
    mock_engine = _make_local_engine(transcript="Hello world")

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    txt = tmp_path / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Hello world"


def test_transcribe_fallback_respects_output_dir(tmp_path):
    """Fallback writes to --output-dir when provided."""
    make_wav(tmp_path / "speech.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine(transcript="Output text")

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "speech.txt"
    assert txt.exists()
    assert txt.read_text() == "Output text"


def test_transcribe_fallback_passes_model_and_language(tmp_path):
    """--model and --language are forwarded to LocalEngine."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--model", "large-v3", "--language", "en"])

    mock_le_cls.assert_called_once()
    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("model") == "large-v3"
    engine_transcribe_kwargs = mock_engine.transcribe.call_args.kwargs
    assert engine_transcribe_kwargs.get("language") == "en"


def test_transcribe_fallback_passes_engine_to_local_engine(tmp_path):
    """--engine (built-in name) is forwarded to LocalEngine."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    with (
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper"])

    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("engine") == "whisper"


def test_transcribe_fallback_applies_postprocess_pipeline(tmp_path):
    """Fallback applies the postprocess pipeline to raw engine text."""
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"
    mock_engine = _make_local_engine(transcript="hello world")
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = "HELLO WORLD"

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=mock_pipeline),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    mock_pipeline.run.assert_called_once_with("hello world")
    txt = out_dir / "audio.txt"
    assert txt.read_text() == "HELLO WORLD"


def test_transcribe_fallback_uses_default_engine_from_config(tmp_path):
    """When --engine is not passed, reads default_engine from config."""
    make_wav(tmp_path / "audio.wav")
    mock_engine = _make_local_engine()

    from resona_client.config import EngineConfig
    mock_cfg = EngineConfig(engines=[], default_engine="voxtral")

    with (
        patch("resona_client.config.EngineConfig.load", return_value=mock_cfg),
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le_cls,
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path)])

    call_kwargs = mock_le_cls.call_args.kwargs
    assert call_kwargs.get("engine") == "voxtral"


def test_transcribe_warns_when_model_flag_with_live_server(tmp_path):
    """--model should print a warning and be ignored when server is reachable."""
    make_wav(tmp_path / "audio.wav")
    mock_client = make_client()
    entry = _make_resona_api_entry()

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=entry),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--model", "large-v3"])

    assert "ignored" in result.output.lower()
    mock_client.submit_job.assert_called_once()


def test_transcribe_fallback_continues_on_per_file_error(tmp_path):
    """A transcription error on one file does not abort processing of others."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    mock_engine = _make_local_engine()
    mock_engine.transcribe.side_effect = [
        httpx.RequestError("connection refused"),
        {"text": "second file ok", "language": "de", "segments": []},
    ]

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path)])

    assert mock_engine.transcribe.call_count == 2
    assert result.exit_code == 0


def test_transcribe_fallback_with_real_postprocess_config(tmp_path):
    """Full fallback chain: LocalEngine returns text, real pipeline from config transforms it."""
    import json

    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    replacements_file = tmp_path / "replacements.json"
    replacements_file.write_text(json.dumps([
        {"name": "raw", "replacement": "PROCESSED"},
    ]))
    config_file = tmp_path / "postprocess.json"
    config_file.write_text(json.dumps({
        "steps": [{"type": "replacements", "source": str(replacements_file)}]
    }))

    mock_engine = _make_local_engine(transcript="raw text here")

    from resona_postprocess.sources import build_pipeline_from_config
    real_pipeline = build_pipeline_from_config(config_file)

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=real_pipeline),
    ):
        runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    txt = out_dir / "audio.txt"
    assert txt.exists()
    assert txt.read_text() == "PROCESSED text here"


def test_transcribe_uses_in_process_engine_when_extra_installed(tmp_path):
    """When asr-core + a backend is installed and no engine resolves, use InProcessEngine."""
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "hi", "language": "de", "segments": []}

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", return_value=mock_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    mock_engine.transcribe.assert_called_once()
    assert (out_dir / "audio.txt").read_text() == "hi"


def test_transcribe_falls_back_to_subprocess_when_in_process_unavailable(tmp_path):
    """If InProcessEngine import/init fails, the subprocess LocalEngine is used as fallback."""
    make_wav(tmp_path / "audio.wav")
    out_dir = tmp_path / "out"

    mock_subprocess_engine = _make_local_engine(transcript="from subprocess")

    with (
        patch("resona_cli.transcribe.resolve_engine", return_value=None),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("asr-core missing")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_subprocess_engine),
        patch("resona_postprocess.sources.build_pipeline_from_config", return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--output-dir", str(out_dir)])

    assert result.exit_code == 0
    mock_subprocess_engine.transcribe.assert_called_once()
    assert (out_dir / "audio.txt").read_text() == "from subprocess"


# ── Unified --engine routing ──────────────────────────────────────────────────

from resona_client.config import EngineConfig, EngineEntry
from resona_cli.engines import BUILTIN_ENGINES  # noqa: F401  (sanity import)


def test_transcribe_engine_pins_cloud_entry(tmp_path, monkeypatch):
    """--engine NAME naming a cloud config entry routes to CloudEngine."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[
        EngineEntry(name="dg", type="cloud", provider="deepgram"),
    ])
    mock_engine = MagicMock()
    mock_engine.transcribe.return_value = {"text": "cloud text", "language": "de", "segments": []}

    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.CloudEngine", return_value=mock_engine) as mock_cls,
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "dg"])

    assert result.exit_code == 0
    mock_cls.assert_called_once()
    mock_engine.transcribe.assert_called_once()


def test_transcribe_engine_pins_local_builtin(tmp_path):
    """--engine faster-whisper routes to the local fallback engine."""
    make_wav(tmp_path / "a.wav")
    mock_engine = _make_local_engine(transcript="local text")

    with (
        patch("resona_client.client.ResonaClient.from_config",
              side_effect=RuntimeError("no server")),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("x")),
        patch("resona_cli.transcribe.LocalEngine", return_value=mock_engine) as mock_le,
        patch("resona_postprocess.sources.build_pipeline_from_config",
              return_value=_noop_pipeline()),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "whisper"])

    assert result.exit_code == 0
    assert mock_le.call_args.kwargs.get("engine") == "whisper"


def test_transcribe_engine_unknown_name_errors(tmp_path):
    make_wav(tmp_path / "a.wav")
    with patch("resona_client.config.EngineConfig.load", return_value=EngineConfig()):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_transcribe_engine_pins_resona_api_entry(tmp_path):
    """--engine NAME naming a resona-api entry routes to ResonaClient."""
    make_wav(tmp_path / "a.wav")
    cfg = EngineConfig(engines=[EngineEntry(name="srv", api_url="http://srv:7000")])
    mock_client = make_client()

    with (
        patch("resona_client.config.EngineConfig.load", return_value=cfg),
        patch("resona_cli.transcribe.ResonaClient", return_value=mock_client),
    ):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--engine", "srv"])

    assert result.exit_code == 0
    mock_client.submit_job.assert_called_once()
