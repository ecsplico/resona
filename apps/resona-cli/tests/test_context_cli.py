"""`--context-file` on job commands: validated, sent to the hub, applied with --local."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import resona_postprocess.llm as llm_mod
from resona_cli import settings_cache
from resona_cli.main import app
from resona_client.client import JobRef, JobView
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()
CONTEXT = {"before": "Im Befund steht:", "selection": "", "after": "Weiter."}


@pytest.fixture(autouse=True)
def _hub(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr(settings_cache, "CACHE_DIR", tmp_path / "settings-cache")
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")
    monkeypatch.setenv("RESONA_API_KEY", "key")


def _client() -> MagicMock:
    client = MagicMock()
    client.submit_job.return_value = JobRef(job_id=1, status="pending")
    client.get_job.return_value = JobView.from_dict({
        "job_id": 1, "kind": "audio", "status": "pending", "private": True,
        "execution_profile": "cpu-private", "candidate": None, "error": None,
    })
    return client


def _context_file(tmp_path, data) -> str:
    path = tmp_path / "context.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("command, filename, extra", [
    ("transcribe", "memo.wav", []),
    ("doc", "scan.pdf", []),
    ("postprocess", "notes.md", ["--profile", "cleanup"]),
])
def test_job_commands_send_the_context_parts_with_text(tmp_path, command, filename, extra):
    source = tmp_path / filename
    source.write_bytes(b"x")
    client = _client()
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, [command, str(source), "--no-wait",
                                     "--context-file", _context_file(tmp_path, CONTEXT), *extra])
    assert result.exit_code == 0, result.output
    assert client.submit_job.call_args.kwargs["context"] == {"before": "Im Befund steht:", "after": "Weiter."}


def test_without_the_flag_no_context_is_sent(tmp_path):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"x")
    client = _client()
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["transcribe", str(source), "--no-wait"])
    assert result.exit_code == 0, result.output
    assert client.submit_job.call_args.kwargs["context"] is None


@pytest.mark.parametrize("data", [{}, ["before"], {"before": "x" * 4001}, {"cursor": "x"}])
def test_invalid_context_file_fails_before_any_hub_call(tmp_path, data):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"x")
    client = _client()
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["transcribe", str(source), "--no-wait",
                                     "--context-file", _context_file(tmp_path, data)])
    assert result.exit_code == 1
    client.submit_job.assert_not_called()


def test_local_postprocess_sends_context_to_the_llm_step(tmp_path, monkeypatch):
    note = tmp_path / "befund.md"
    note.write_text("neuer satz", encoding="utf-8")
    profile = tmp_path / "cleanup.json"
    profile.write_text(json.dumps({"name": "cleanup", "steps": [
        {"type": "llm", "name": "cleanup", "prompt": "Clean up.", "model": "ollama/llama3"},
    ]}), encoding="utf-8")
    users: list[str] = []

    def completion(**kwargs):
        users.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Neuer Satz."))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", SimpleNamespace(completion=completion))

    result = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile),
                                 "--context-file", _context_file(tmp_path, CONTEXT)])

    assert result.exit_code == 0, result.output
    assert users == ["<text>\nneuer satz\n</text>\n\n<context_before>\nIm Befund steht:\n</context_before>"
                     "\n\n<context_after>\nWeiter.\n</context_after>"]
    stored = JobResult.model_validate_json((tmp_path / "befund.cleanup.result.json").read_text())
    assert stored.markdown == "Neuer Satz."


def test_local_context_needs_an_llm_step(tmp_path):
    note = tmp_path / "befund.md"
    note.write_text("Befund Komma ok", encoding="utf-8")
    profile = tmp_path / "fix.json"
    profile.write_text(json.dumps({"name": "fix", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
    ]}), encoding="utf-8")

    result = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile),
                                 "--context-file", _context_file(tmp_path, CONTEXT)])

    assert result.exit_code == 1
    assert "invalid_context" in result.output
