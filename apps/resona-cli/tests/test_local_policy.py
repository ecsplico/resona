"""Local CLI fallback runs are private by default; cloud LLM stages need --no-private."""
import io
import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import resona_postprocess.llm as llm_mod
from resona_cli.main import app

runner = CliRunner()

CLOUD_PROFILE = {
    "name": "cloud-polish",
    "steps": [{"type": "llm", "name": "polish", "prompt": "fix", "model": "gpt-4o-mini"}],
}


def _make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def _profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "cloud-polish.json"
    path.write_text(json.dumps(CLOUD_PROFILE), encoding="utf-8")
    return path


class _RecordingLiteLLM:
    def __init__(self):
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="FORMATTED")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def litellm_spy(monkeypatch):
    spy = _RecordingLiteLLM()
    monkeypatch.setattr(llm_mod, "litellm", spy)
    return spy


def _local_engine_mock():
    engine = MagicMock()
    engine.transcribe.return_value = {"text": "raw text", "language": "de", "segments": []}
    engine.__enter__.return_value = engine
    engine.__exit__.return_value = False
    return engine


def _gateway_unavailable():
    return patch(
        "resona_client.client.ResonaClient.from_config",
        side_effect=RuntimeError("no server"),
    )


def test_transcribe_local_default_denies_cloud_llm_before_any_call(tmp_path, litellm_spy):
    _make_wav(tmp_path / "audio.wav")
    profile = _profile_file(tmp_path)

    with _gateway_unavailable():
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--profile", str(profile)])

    assert result.exit_code == 2
    assert "policy_denied: polish:" in result.output
    assert litellm_spy.calls == []


def test_transcribe_local_no_private_allows_cloud_llm_and_writes_output(tmp_path, litellm_spy):
    _make_wav(tmp_path / "audio.wav")
    profile = _profile_file(tmp_path)
    out_dir = tmp_path / "out"
    engine = _local_engine_mock()

    with (
        _gateway_unavailable(),
        patch("resona_cli.transcribe.InProcessEngine", side_effect=ImportError("no asr-core")),
        patch("resona_cli.transcribe.LocalEngine", return_value=engine),
    ):
        result = runner.invoke(
            app,
            ["transcribe", str(tmp_path), "--profile", str(profile),
             "--output-dir", str(out_dir), "--no-private"],
        )

    assert result.exit_code == 0, result.output
    assert len(litellm_spy.calls) == 1
    assert litellm_spy.calls[0]["model"] == "gpt-4o-mini"
    assert "FORMATTED" in (out_dir / "audio.md").read_text(encoding="utf-8")


def test_watch_local_fallback_denies_cloud_llm_before_watching(tmp_path, litellm_spy):
    profile = _profile_file(tmp_path)
    engine = _local_engine_mock()

    with (
        _gateway_unavailable(),
        patch("resona_cli.watch.LocalEngine", return_value=engine),
        patch("resona_cli.watch.time.sleep", side_effect=AssertionError("must not loop")),
    ):
        result = runner.invoke(app, ["watch", str(tmp_path), "--profile", str(profile)])

    assert result.exit_code == 2
    assert "policy_denied: polish:" in result.output
    assert litellm_spy.calls == []
    engine.transcribe.assert_not_called()
