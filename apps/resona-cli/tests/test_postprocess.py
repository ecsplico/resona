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
        "execution_profile": "-", "candidate": None})
    rerun = JobResult(kind="text", raw_text="Befund Komma ok", markdown="Befund, ok",
                      raw_markdown="Befund Komma ok", language="de", provenance={"stages": []})
    client.get_result.side_effect = [client.get_result.return_value, rerun]

    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["postprocess", "--job", "12", "--profile", "transcript-markdown-de", "--json"])

    assert result.exit_code == 0, result.output
    assert client.get_result.call_args_list[0].args == (12,)
    kwargs = client.submit_job.call_args.kwargs
    assert (kwargs["kind"], kwargs["profile"], kwargs["private"]) == ("text", "transcript-markdown-de", True)
    assert (kwargs["engine"], kwargs["model"], kwargs["execution_profile"]) == (None, None, None)
    assert kwargs["language"] == "de"  # the earlier job's language
    assert uploaded == ["Befund Komma ok"]
    # The profile is part of the output name; the input is the job, not the deleted temp file.
    markdown = (tmp_path / "job-12.transcript-markdown-de.md").read_text()
    assert markdown.rstrip().endswith("Befund, ok") and "job:12" in markdown
    assert json.loads((tmp_path / "job-12.transcript-markdown-de.result.json").read_text())["kind"] == "text"
    (record,) = json.loads(result.stdout)["jobs"]
    assert record["input"] == "job:12" and record["markdown"].endswith("job-12.transcript-markdown-de.md")
    assert "candidate=none" in result.stderr


def test_postprocess_needs_files_or_job_but_not_both(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("x", encoding="utf-8")
    for args in ([], [str(note), "--job", "1"]):
        result = runner.invoke(app, ["postprocess", *args, "--profile", "fix"])
        assert result.exit_code == 1 and "either text files or --job" in result.output


def test_postprocess_writes_profile_named_outputs_next_to_the_input(tmp_path, monkeypatch):
    note = tmp_path / "befund.md"
    note.write_text("Befund Komma ok\n", encoding="utf-8")
    profile = tmp_path / "fix.json"
    profile.write_text(json.dumps(RULES), encoding="utf-8")
    execution = MagicMock()
    monkeypatch.setattr("resona_cli.local_run.subprocess.run", execution)

    result = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile)])

    assert result.exit_code == 0, result.output
    execution.assert_not_called()
    assert note.read_text(encoding="utf-8") == "Befund Komma ok\n"
    stored = JobResult.model_validate_json((tmp_path / "befund.fix.result.json").read_text())
    assert (stored.kind, stored.raw_text, stored.markdown) == ("text", "Befund Komma ok\n", "Befund, ok\n")
    assert stored.raw_markdown == "Befund Komma ok\n" and stored.provenance.worker_id == "local"
    assert (tmp_path / "befund.fix.md").read_text().rstrip().endswith("Befund, ok")

    # Existing outputs are kept unless --force; with --force they are replaced.
    again = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile)])
    assert again.exit_code == 1 and "use --force" in again.output
    forced = runner.invoke(app, ["postprocess", str(note), "--local", "--profile", str(profile), "--force"])
    assert forced.exit_code == 0, forced.output


@pytest.mark.parametrize(("profile", "label"), [
    ("transcript-markdown-de", "transcript-markdown-de"),
    ("profiles/fix.json", "fix"),
    ('{"name": "inline fix", "steps": []}', "inline-fix"),
    ('{"steps": []}', "postprocess"),
])
def test_profile_label_names_outputs_by_profile(profile, label):
    from resona_cli.postprocess import profile_label

    assert profile_label(profile) == label
