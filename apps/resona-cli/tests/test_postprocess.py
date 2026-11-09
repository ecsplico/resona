"""`resona postprocess`: text jobs that run only a postprocess profile."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli import settings_cache
from resona_cli.main import app
from resona_client.client import JobRef, JobView
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()

RULES = {"name": "fix", "steps": [{"type": "replacements", "rules": [
    {"pattern": r"\s*\bKomma\b", "replacement": ","},
]}]}


@pytest.fixture(autouse=True)
def _hub(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr(settings_cache, "CACHE_DIR", tmp_path / "settings-cache")
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def test_postprocess_job_reruns_profile_on_unpolished_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    uploaded: list[str] = []
    client = MagicMock()
    client.get_result.return_value = JobResult(
        kind="audio", raw_text="Befund Komma ok", markdown="polished by an older profile",
        raw_markdown="Befund Komma ok", language="de", provenance={"stages": []})

    def submit(path, **kwargs):
        uploaded.append(Path(path).read_text(encoding="utf-8"))
        return JobRef(job_id=13, status="pending")

    client.submit_job.side_effect = submit
    client.wait_for_job.return_value = JobView.from_dict({
        "job_id": 13, "kind": "text", "status": "completed", "private": True,
        "execution_profile": "postprocess", "candidate": None})
    rerun = JobResult(kind="text", raw_text="Befund Komma ok", markdown="Befund, ok",
                      raw_markdown="Befund Komma ok", language="de", provenance={"stages": []})
    client.get_result.side_effect = [client.get_result.return_value, rerun]

    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["postprocess", "--job", "12", "--profile", "transcript-markdown-de"])

    assert result.exit_code == 0, result.output
    assert client.get_result.call_args_list[0].args == (12,)
    kwargs = client.submit_job.call_args.kwargs
    assert (kwargs["kind"], kwargs["profile"], kwargs["private"]) == ("text", "transcript-markdown-de", True)
    assert (kwargs["engine"], kwargs["model"], kwargs["execution_profile"]) == (None, None, None)
    assert kwargs["language"] == "de"  # the earlier job's language
    assert uploaded == ["Befund Komma ok"]
    assert (tmp_path / "job-12.md").read_text().rstrip().endswith("Befund, ok")
    assert json.loads((tmp_path / "job-12.result.json").read_text())["kind"] == "text"
    assert "candidate=none" in result.output


def test_postprocess_needs_files_or_job_but_not_both(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("x", encoding="utf-8")
    for args in ([], [str(note), "--job", "1"]):
        result = runner.invoke(app, ["postprocess", *args, "--profile", "fix"])
        assert result.exit_code == 1 and "either text files or --job" in result.output


def test_postprocess_local_runs_profile_in_process_without_overwriting_the_input(tmp_path, monkeypatch):
    note = tmp_path / "befund.md"
    note.write_text("Befund Komma ok\n", encoding="utf-8")
    profile = tmp_path / "fix.json"
    profile.write_text(json.dumps(RULES), encoding="utf-8")
    execution = MagicMock()
    monkeypatch.setattr("resona_cli.local_run.subprocess.run", execution)

    clash = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile)])
    assert clash.exit_code == 2 and "would replace the input" in clash.output
    assert note.read_text(encoding="utf-8") == "Befund Komma ok\n"

    out = tmp_path / "out"
    out.mkdir()
    result = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile),
                                 "--output-dir", str(out)])

    assert result.exit_code == 0, result.output
    execution.assert_not_called()
    stored = JobResult.model_validate_json((out / "befund.result.json").read_text())
    assert (stored.kind, stored.raw_text, stored.markdown) == ("text", "Befund Komma ok\n", "Befund, ok\n")
    assert stored.raw_markdown == "Befund Komma ok\n" and stored.provenance.worker_id == "local"
