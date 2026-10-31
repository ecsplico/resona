"""`resona watch`: each new audio file becomes a private hub job; no local fallback."""
import io
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import JobRef, JobView
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr("resona_cli.settings_cache.CACHE_DIR", tmp_path / "settings-cache")


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def _wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


def _stop_after(n):
    calls = {"n": 0}

    def sleep(_):
        calls["n"] += 1
        if calls["n"] >= n:
            raise KeyboardInterrupt
    return sleep


def _client(status="completed", error=None):
    client = MagicMock()
    ids = iter(range(1, 100))
    client.submit_job.side_effect = lambda *a, **k: JobRef(job_id=next(ids), status="pending")
    client.wait_for_job.side_effect = lambda job_id, **k: JobView.from_dict({
        "job_id": job_id, "kind": "audio", "status": status, "private": True,
        "execution_profile": "private-auto", "error": error})
    client.get_result.return_value = JobResult(
        kind="audio", raw_text="watched", markdown="Watched", language="de",
        provenance={"stages": [{"stage": "asr", "engine": "e", "model": "m",
                                "accelerator": "cpu", "seconds": 1.0}]})
    return client


def _watch(client, args, sleeps=1):
    with (
        patch("resona_client.client.ResonaClient.from_config", return_value=client),
        patch("resona_cli.watch.time.sleep", side_effect=_stop_after(sleeps)),
    ):
        return runner.invoke(app, ["watch", *args])


def test_watch_writes_private_job_results(tmp_path, hub):
    _wav(tmp_path / "a.wav")
    (tmp_path / "notes.txt").write_text("x")
    client = _client()

    result = _watch(client, [str(tmp_path), "--execution-profile", "cpu-private"])

    assert result.exit_code == 0, result.output
    kwargs = client.submit_job.call_args.kwargs
    assert kwargs["private"] is True and kwargs["execution_profile"] == "cpu-private"
    assert client.submit_job.call_count == 1
    assert (tmp_path / "a.md").read_text().rstrip().endswith("Watched")
    assert (tmp_path / "a.result.json").exists()


def test_watch_submits_each_file_once_and_skips_existing_outputs(tmp_path, hub):
    _wav(tmp_path / "a.wav")
    _wav(tmp_path / "done.wav")
    (tmp_path / "done.md").write_text("already")
    client = _client()

    result = _watch(client, [str(tmp_path)], sleeps=3)

    assert result.exit_code == 0, result.output
    assert client.submit_job.call_count == 1
    assert (tmp_path / "done.md").read_text() == "already"


def test_watch_reports_failed_job_and_keeps_watching(tmp_path, hub):
    _wav(tmp_path / "a.wav")
    client = _client(status="failed", error={"code": "engine_error", "message": "boom"})

    result = _watch(client, [str(tmp_path)], sleeps=2)

    assert result.exit_code == 0
    assert "job 1 failed: boom" in result.output
    assert not (tmp_path / "a.md").exists()


def test_watch_hub_error_never_falls_back_to_local(tmp_path, hub):
    _wav(tmp_path / "a.wav")
    client = _client()
    client.wait_for_job.side_effect = httpx.ConnectError("down")

    with patch("resona_cli.local_run.run_local") as local_run:
        result = _watch(client, [str(tmp_path)])

    assert result.exit_code == 0
    assert "hub request failed" in result.output
    local_run.assert_not_called()


def test_watch_policy_denied_stops_with_exit_2(tmp_path, hub):
    _wav(tmp_path / "a.wav")
    client = _client(status="failed", error={"code": "policy_denied", "message": "llm: denied"})

    result = _watch(client, [str(tmp_path)], sleeps=5)

    assert result.exit_code == 2


def test_watch_without_hub_requires_explicit_local(tmp_path):
    _wav(tmp_path / "a.wav")
    with patch("resona_cli.local_run.run_local") as local_run:
        result = runner.invoke(app, ["watch", str(tmp_path)])

    assert result.exit_code == 1
    assert "no hub configured" in result.output
    local_run.assert_not_called()
