"""`resona jobs get|wait|result|cancel|delete|list`."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import JobNotReady, JobPage, JobTimeout, JobView, ResonaAPIError, ResultExpired
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hub(monkeypatch):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def _view(job_id=9, status="completed", error=None):
    return JobView.from_dict({"job_id": job_id, "kind": "audio", "status": status, "private": True,
                              "execution_profile": "cpu-private", "error": error,
                              "queue_reason": "no_capable_worker" if status == "pending" else None})


RESULT = JobResult(kind="audio", raw_text="hallo", markdown="Hallo", language="de",
                   provenance={"stages": [{"stage": "asr", "engine": "faster-whisper", "model": "m",
                                           "accelerator": "cpu", "seconds": 1.0}]})


def _run(client, *args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, ["jobs", *args])


def test_get_prints_job_json():
    client = MagicMock()
    client.get_job.return_value = _view(status="pending")

    result = _run(client, "get", "9")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["queue_reason"] == "no_capable_worker"


def test_get_unknown_job_is_not_found():
    client = MagicMock()
    client.get_job.side_effect = ResonaAPIError(404, "job_not_found", "no such job")

    result = _run(client, "get", "9")

    assert result.exit_code == 1
    assert "not_found" in result.output


@pytest.mark.parametrize("view, code", [
    (_view(), 0),
    (_view(status="failed", error={"code": "engine_error", "message": "x"}), 1),
    (_view(status="failed", error={"code": "policy_denied", "message": "x"}), 2),
])
def test_wait_exit_code_follows_outcome(view, code):
    client = MagicMock()
    client.wait_for_job.return_value = view

    result = _run(client, "wait", "9", "--timeout", "3")

    assert result.exit_code == code
    assert json.loads(result.stdout)["status"] == view.status
    assert client.wait_for_job.call_args.kwargs["timeout"] == 3


def test_wait_timeout_exits_1():
    client = MagicMock()
    client.wait_for_job.side_effect = JobTimeout(9)

    result = _run(client, "wait", "9", "--timeout", "1")

    assert result.exit_code == 1
    assert "still running" in result.output


def test_result_prints_markdown_or_json():
    client = MagicMock()
    client.get_job.return_value = _view()
    client.get_result.return_value = RESULT

    assert _run(client, "result", "9").stdout.strip() == "Hallo"
    assert json.loads(_run(client, "result", "9", "--json").stdout)["raw_text"] == "hallo"


def test_result_writes_files_without_overwriting(tmp_path):
    client = MagicMock()
    client.get_job.return_value = _view()
    client.get_result.return_value = RESULT

    first = _run(client, "result", "9", "--output-dir", str(tmp_path), "--stem", "memo")
    second = _run(client, "result", "9", "--output-dir", str(tmp_path), "--stem", "memo")

    assert first.exit_code == 0, first.output
    assert (tmp_path / "memo.md").read_text().rstrip().endswith("Hallo")
    assert JobResult.model_validate_json((tmp_path / "memo.result.json").read_text()) == RESULT
    assert second.exit_code == 1 and "--force" in second.output


@pytest.mark.parametrize("error, message", [
    (JobNotReady(409, "job_not_ready", "pending"), "job_not_ready"),
    (ResultExpired(410, "result_expired", "gone"), "result_expired"),
    (ResultExpired(410, "content_unavailable", "gone"), "content_unavailable"),
])
def test_result_not_ready_or_expired(error, message):
    client = MagicMock()
    client.get_job.return_value = _view()
    client.get_result.side_effect = error

    result = _run(client, "result", "9")

    assert result.exit_code == 1
    assert message in result.output


def test_cancel_and_delete():
    client = MagicMock()
    client.cancel_job.return_value = _view(status="cancelled")

    cancelled = _run(client, "cancel", "9")
    deleted = _run(client, "delete", "9")

    assert json.loads(cancelled.stdout)["status"] == "cancelled"
    assert deleted.exit_code == 0
    client.delete_job.assert_called_once_with(9)


def test_list_table_and_json():
    client = MagicMock()
    client.list_jobs.return_value = JobPage(items=[_view(9), _view(8, status="pending")], next_cursor="c2")

    table = _run(client, "list", "--limit", "2")
    as_json = _run(client, "list", "--json", "--cursor", "c1")

    assert table.exit_code == 0, table.output
    assert table.stdout.splitlines()[1].startswith("8\taudio\tpending (no_capable_worker)")
    assert "--cursor c2" in table.stderr
    assert [i["job_id"] for i in json.loads(as_json.stdout)["items"]] == [9, 8]
    assert client.list_jobs.call_args.kwargs == {"cursor": "c1", "limit": 50}
