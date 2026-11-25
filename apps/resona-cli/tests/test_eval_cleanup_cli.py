"""`resona eval cleanup`: judge-role preflight, report contents and role_unassigned mapping."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import JobRef, JobView, ResonaAPIError, Upload
from resona_client.config import EngineConfig
from resona_job_core import JobResult, Provenance

runner = CliRunner()
CASES = {"cases": [
    {"id": "de-a", "transcript": "am freitag nein streich das am montag", "before": "Notiz. ",
     "rubric": "Montag, kein Freitag.", "includes": ["montag"], "excludes": ["freitag"]},
    {"id": "de-b", "transcript": "die dosis erhöhen", "selection": "die Dosis beibehalten",
     "rubric": "Nur die Erhöhung.", "includes": ["erhöhen"], "excludes": ["beibehalten"]},
]}


@pytest.fixture(autouse=True)
def _hub(monkeypatch):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")
    monkeypatch.setenv("RESONA_API_KEY", "key")


class FakeHub:
    """Text jobs complete at once: cleanup jobs return ``outputs[case]``, judge jobs ``verdicts[case]``."""

    def __init__(self, *, roles=None, outputs=None, verdicts=None, submit_error=None):
        self.roles = {"judge": "local-llm", "cleanup": "local-llm"} if roles is None else roles
        self.outputs = outputs or {}
        self.verdicts = verdicts or {}
        self.submit_error = submit_error
        self.uploads: dict[str, str] = {}
        self.submitted: list[dict] = []
        self.results: dict[int, JobResult] = {}

    def get_settings(self):
        return SimpleNamespace(settings={"model_roles": self.roles})

    def upload_bytes(self, filename, data):
        self.uploads[filename] = data.decode("utf-8")
        return Upload(upload_id=filename, sha256="s", size=len(data))

    def submit_upload(self, upload_id, **kwargs):
        if self.submit_error is not None:
            raise self.submit_error
        job_id = len(self.submitted) + 1
        self.submitted.append({"upload_id": upload_id, **kwargs})
        role, case = upload_id.removesuffix(".txt").split("-", 1)
        if role == "cleanup":
            result = JobResult(kind="text", raw_text="", markdown=self.outputs[case], provenance=Provenance())
        else:
            result = JobResult(kind="text", raw_text="", markdown="", provenance=Provenance(),
                               structured={"judge": self.verdicts[case]})
        self.results[job_id] = result
        return JobRef(job_id=job_id, status="pending")

    def wait_for_job(self, job_id, timeout=None):
        return JobView.from_dict({"job_id": job_id, "kind": "text", "status": "completed", "private": True})

    def get_result(self, job_id):
        return self.results[job_id]

    def close(self):
        pass


def _invoke(tmp_path, hub, *extra):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(CASES), encoding="utf-8")
    with patch("resona_client.client.ResonaClient.from_config", return_value=hub):
        return runner.invoke(app, ["eval", "cleanup", "--profile", "dictation-clean-de", "--cases", str(cases),
                                   "--output-dir", str(tmp_path / "out"), *extra])


def test_unassigned_judge_role_stops_before_any_job(tmp_path):
    hub = FakeHub(roles={"cleanup": "local-llm"})
    result = _invoke(tmp_path, hub)
    assert result.exit_code == 1
    assert "model role 'judge' is not assigned" in result.stderr
    assert "model_roles.judge" in result.stderr and "resona settings set --file" in result.stderr
    assert hub.uploads == {} and hub.submitted == []


def test_run_writes_a_measurement_only_report(tmp_path):
    hub = FakeHub(
        outputs={"de-a": "Am Montag.", "de-b": "Die Dosis beibehalten und erhöhen."},
        verdicts={"de-a": {"pass": True, "reason": "erfüllt"}, "de-b": "kein objekt"},
    )
    result = _invoke(tmp_path, hub)
    assert result.exit_code == 0, result.stderr
    report = json.loads((tmp_path / "out" / "cleanup-report.json").read_text(encoding="utf-8"))
    assert report["kind"] == "cleanup" and report["outcome"] == "measurement_only"
    assert (report["profile"], report["judge_profile"]) == ("dictation-clean-de", "cleanup-judge-de")
    assert (report["passed"], report["total"]) == (1, 2)
    a, b = report["cases"]
    assert a["output"] == "Am Montag." and a["judge"] == {"pass": True, "reason": "erfüllt"} and a["pass"]
    assert b["present_excludes"] == ["beibehalten"]
    assert b["judge"]["pass"] is False and b["judge"]["reason"].startswith("judge output invalid: ")
    assert "passed 1/2" in result.stderr and "failed de-b" in result.stderr

    cleanup_jobs = [job for job in hub.submitted if job["upload_id"].startswith("cleanup-")]
    judge_jobs = [job for job in hub.submitted if job["upload_id"].startswith("judge-")]
    assert [job["context"] for job in cleanup_jobs] == [{"before": "Notiz. "}, {"selection": "die Dosis beibehalten"}]
    assert all(job["kind"] == "text" and job["private"] is True for job in hub.submitted)
    assert {job["profile"] for job in cleanup_jobs} == {"dictation-clean-de"}
    assert {job["profile"] for job in judge_jobs} == {"cleanup-judge-de"}
    judge_input = hub.uploads["judge-de-a.txt"]
    assert "Montag, kein Freitag." in judge_input and "Am Montag." in judge_input and "Notiz. " in judge_input


def test_case_filter_runs_only_the_named_cases(tmp_path):
    hub = FakeHub(outputs={"de-b": "erhöhen"}, verdicts={"de-b": {"pass": True, "reason": "ok"}})
    result = _invoke(tmp_path, hub, "--case", "de-b")
    assert result.exit_code == 0, result.stderr
    report = json.loads((tmp_path / "out" / "cleanup-report.json").read_text(encoding="utf-8"))
    assert [entry["id"] for entry in report["cases"]] == ["de-b"]
    assert _invoke(tmp_path, FakeHub(), "--case", "nope").exit_code == 1


def test_role_unassigned_from_the_hub_names_the_role(tmp_path):
    error = ResonaAPIError(422, "role_unassigned",
                           "model role 'cleanup' is not assigned to an endpoint in the organization settings")
    result = _invoke(tmp_path, FakeHub(submit_error=error))
    assert result.exit_code == 1
    assert "model role 'cleanup' is not assigned" in result.stderr
    assert "model_roles.cleanup" in result.stderr
    assert not (tmp_path / "out" / "cleanup-report.json").exists()
