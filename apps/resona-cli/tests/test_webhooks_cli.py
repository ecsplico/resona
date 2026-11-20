"""`resona webhooks add|list|rotate|remove|deliveries` and `--webhook` on job commands."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import (
    JobRef,
    JobView,
    ResonaAPIError,
    Webhook,
    WebhookCreated,
    WebhookDelivery,
)
from resona_client.config import EngineConfig

runner = CliRunner()
SECRET = "whsec_c2VjcmV0LWJ5dGVzLWZvci10ZXN0cw=="
ROTATED = "whsec_cm90YXRlZC1zZWNyZXQ="


@pytest.fixture(autouse=True)
def _hub(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")
    monkeypatch.setenv("RESONA_API_KEY", "admin-key")
    monkeypatch.setattr("resona_cli.settings_cache.CACHE_DIR", tmp_path / "settings-cache")


def _client() -> MagicMock:
    client = MagicMock()
    client.create_webhook.return_value = WebhookCreated(
        name="backend", url="https://hooks.example/resona", created_at="2026-11-20T10:00:00Z", secret=SECRET)
    client.rotate_webhook.return_value = WebhookCreated(
        name="backend", url="https://hooks.example/resona", created_at="2026-11-20T10:00:00Z", secret=ROTATED)
    client.list_webhooks.return_value = [
        Webhook(name="backend", url="https://hooks.example/resona", created_at="2026-11-20T10:00:00Z"),
    ]
    client.list_webhook_deliveries.return_value = [
        WebhookDelivery(id="d2", job_id=8, event="job.failed", attempts=3, last_status=500,
                        delivered_at=None, last_error="http_status"),
        WebhookDelivery(id="d1", job_id=7, event="job.completed", attempts=1, last_status=204,
                        delivered_at="2026-11-20T10:00:01Z", last_error=None),
    ]
    ids = iter(range(1, 100))
    client.submit_job.side_effect = lambda *a, **k: JobRef(job_id=next(ids), status="pending")
    client.get_job.side_effect = lambda job_id: JobView.from_dict({
        "job_id": job_id, "kind": "audio", "status": "pending", "private": True,
        "execution_profile": "cpu-private", "candidate": None, "error": None, "webhook": "backend",
    })
    return client


def _run(client, *args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, list(args))


# ── resona webhooks ───────────────────────────────────────────────────────────

def test_add_prints_secret_alone_on_stdout():
    client = _client()
    result = _run(client, "webhooks", "add", "backend", "https://hooks.example/resona")
    assert result.exit_code == 0, result.output
    client.create_webhook.assert_called_once_with("backend", "https://hooks.example/resona")
    assert result.stdout.strip() == SECRET
    assert SECRET not in result.stderr
    assert "shown once" in result.stderr


def test_rotate_prints_new_secret_alone_on_stdout():
    client = _client()
    result = _run(client, "webhooks", "rotate", "backend")
    assert result.exit_code == 0, result.output
    client.rotate_webhook.assert_called_once_with("backend")
    assert result.stdout.strip() == ROTATED


@pytest.mark.parametrize("args", [
    ["list"], ["list", "--json"], ["deliveries", "backend"], ["deliveries", "backend", "--json"],
    ["remove", "backend"],
])
def test_other_commands_never_print_a_secret(args):
    result = _run(_client(), "webhooks", *args)
    assert result.exit_code == 0, result.output
    assert "whsec_" not in result.output


def test_list_prints_one_line_per_endpoint():
    result = _run(_client(), "webhooks", "list")
    assert result.stdout.splitlines() == ["backend\thttps://hooks.example/resona\t2026-11-20T10:00:00Z"]


def test_list_json():
    result = _run(_client(), "webhooks", "list", "--json")
    assert json.loads(result.stdout) == [
        {"name": "backend", "url": "https://hooks.example/resona", "created_at": "2026-11-20T10:00:00Z"},
    ]


def test_remove_deletes_endpoint():
    client = _client()
    result = _run(client, "webhooks", "remove", "backend")
    assert result.exit_code == 0, result.output
    client.delete_webhook.assert_called_once_with("backend")


def test_deliveries_passes_limit_and_prints_newest_first():
    client = _client()
    result = _run(client, "webhooks", "deliveries", "backend", "--limit", "2")
    assert result.exit_code == 0, result.output
    client.list_webhook_deliveries.assert_called_once_with("backend", limit=2)
    lines = result.stdout.splitlines()
    assert lines[0].startswith("d2\tjob 8\tjob.failed\tattempts=3\tstatus=500\thttp_status")
    assert lines[1].endswith("delivered 2026-11-20T10:00:01Z")


def test_deliveries_json():
    result = _run(_client(), "webhooks", "deliveries", "backend", "--json")
    rows = json.loads(result.stdout)
    assert [r["id"] for r in rows] == ["d2", "d1"]
    assert rows[1]["last_status"] == 204


def test_add_reports_hub_error_code():
    client = _client()
    client.create_webhook.side_effect = ResonaAPIError(422, "invalid_webhook_url", "internal address")
    result = _run(client, "webhooks", "add", "backend", "http://10.0.0.1/")
    assert result.exit_code == 1
    assert "invalid_webhook_url" in result.stderr
    assert result.stdout == ""


def test_rotate_unknown_name_exits_1():
    client = _client()
    client.rotate_webhook.side_effect = ResonaAPIError(404, "webhook_not_found", "no")
    result = _run(client, "webhooks", "rotate", "gone")
    assert result.exit_code == 1
    assert "webhook_not_found" in result.stderr


# ── --webhook on job commands ─────────────────────────────────────────────────

@pytest.mark.parametrize("command, filename, extra", [
    ("transcribe", "memo.wav", []),
    ("doc", "scan.pdf", []),
    ("postprocess", "notes.md", ["--profile", "transcript-polish-de"]),
])
def test_job_commands_pass_webhook_to_submit_job(tmp_path, command, filename, extra):
    source = tmp_path / filename
    source.write_bytes(b"x")
    client = _client()
    result = _run(client, command, str(source), "--no-wait", "--webhook", "backend", *extra)
    assert result.exit_code == 0, result.output
    assert client.submit_job.call_args.kwargs["webhook"] == "backend"


def test_transcribe_without_webhook_sends_none(tmp_path):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"x")
    client = _client()
    result = _run(client, "transcribe", str(source), "--no-wait")
    assert result.exit_code == 0, result.output
    assert client.submit_job.call_args.kwargs["webhook"] is None


def test_unknown_webhook_is_reported(tmp_path):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"x")
    client = _client()
    client.submit_job.side_effect = ResonaAPIError(422, "unknown_webhook", "no active webhook endpoint has this name")
    result = _run(client, "transcribe", str(source), "--no-wait", "--webhook", "missing")
    assert result.exit_code == 1
    assert "unknown_webhook" in result.stderr and "resona webhooks list" in result.stderr


def test_webhook_with_local_is_refused(tmp_path):
    source = tmp_path / "memo.wav"
    source.write_bytes(b"x")
    result = runner.invoke(app, ["transcribe", str(source), "--local", "--webhook", "backend"])
    assert result.exit_code == 1
    assert "--webhook needs the hub" in result.stderr
