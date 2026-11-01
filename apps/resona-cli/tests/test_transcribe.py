"""`resona transcribe`: hub jobs, output files, flags and no-fallback behavior."""
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
from resona_client.client import (
    AudioOptions,
    JobRef,
    JobTimeout,
    JobView,
    PolicyDeniedError,
    ResonaAPIError,
)
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Isolate tests from the host's RESONA_API_URL, config and settings cache."""
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("RESONA_API_KEY", raising=False)
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr("resona_cli.settings_cache.CACHE_DIR", tmp_path / "settings-cache")


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")
    monkeypatch.setenv("RESONA_API_KEY", "client-key")


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


CANDIDATE = {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx",
             "accelerator": "metal", "model_revision": None, "ocr_engine": None}


def job_view(job_id, status="completed", error=None, private=True):
    return JobView.from_dict({
        "job_id": job_id, "kind": "audio", "status": status, "private": private,
        "execution_profile": "mac-private", "candidate": CANDIDATE, "error": error,
    })


def make_result(text="Hallo Welt"):
    return JobResult(
        kind="audio", raw_text=text.lower(), markdown=text, language="de",
        provenance={"stages": [{"stage": "asr", "engine": "mlx-whisper",
                                "model": "mlx-community/whisper-large-v3-mlx",
                                "accelerator": "metal", "seconds": 1.0}],
                    "worker_id": "w-mac", "settings_revision": 3},
    )


def make_client(text="Hallo Welt"):
    client = MagicMock()
    ids = iter(range(1, 100))
    client.submit_job.side_effect = lambda *a, **k: JobRef(job_id=next(ids), status="pending")
    client.wait_for_job.side_effect = lambda job_id, **k: job_view(job_id)
    client.get_job.side_effect = lambda job_id: job_view(job_id, status="pending")
    client.get_result.return_value = make_result(text)
    return client


def invoke(client, args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, ["transcribe", *args])


def test_writes_markdown_and_result_json(tmp_path, hub):
    audio = make_wav(tmp_path / "memo.wav")
    client = make_client()

    result = invoke(client, [str(audio)])

    assert result.exit_code == 0, result.output
    md = (tmp_path / "memo.md").read_text()
    assert md.startswith("---\n") and md.rstrip().endswith("Hallo Welt")
    assert "job_id: 1\n" in md and 'worker: "w-mac"' in md and "settings_revision: 3\n" in md
    assert "private: true\n" in md
    stored = JobResult.model_validate_json((tmp_path / "memo.result.json").read_text())
    assert stored.raw_text == "hallo welt" and stored.markdown == "Hallo Welt"
    assert not list(tmp_path.glob(".*.tmp"))


def test_submits_private_audio_job_with_flags(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()

    result = invoke(client, [str(audio), "--execution-profile", "mac-private", "--profile", "komma",
                             "--engine", "mlx-whisper", "--model", "m", "--language", "en",
                             "--translate", "--output-dir", str(tmp_path / "out")])

    assert result.exit_code == 0, result.output
    kwargs = client.submit_job.call_args.kwargs
    assert kwargs["kind"] == "audio" and kwargs["private"] is True
    assert kwargs["execution_profile"] == "mac-private" and kwargs["profile"] == "komma"
    assert (kwargs["engine"], kwargs["model"], kwargs["language"]) == ("mlx-whisper", "m", "en")
    assert kwargs["options"] == AudioOptions(translate=True)
    assert (tmp_path / "out" / "a.md").exists()


def test_allow_cloud_submits_non_private(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.wait_for_job.side_effect = lambda job_id, **k: job_view(job_id, private=False)

    result = invoke(client, [str(audio), "--allow-cloud"])

    assert result.exit_code == 0, result.output
    assert client.submit_job.call_args.kwargs["private"] is False
    assert "private=false (cloud allowed)" in result.output


def test_private_and_allow_cloud_are_mutually_exclusive(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()

    result = invoke(client, [str(audio), "--private", "--allow-cloud"])

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output
    client.submit_job.assert_not_called()


def test_json_stdout_is_parseable_and_summary_goes_to_stderr(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["transcribe", str(audio), "--json"])

    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    [job] = report["jobs"]
    assert job["job_id"] == 1 and job["status"] == "completed" and job["private"] is True
    assert job["candidate"]["engine"] == "mlx-whisper"
    assert job["markdown"].endswith("a.md") and job["result"].endswith("a.result.json")
    assert ("job 1: private=true execution_profile=mac-private "
            "candidate=mlx-whisper/mlx-community/whisper-large-v3-mlx@metal") in result.stderr


def test_no_wait_prints_job_ids_and_writes_nothing(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    client = make_client()

    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        result = runner.invoke(app, ["transcribe", str(tmp_path), "--no-wait"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.split() == ["1", "2"]
    client.wait_for_job.assert_not_called()
    assert not list(tmp_path.glob("*.md"))
    assert "status=pending" in result.stderr


def test_refuses_to_overwrite_without_force(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    (tmp_path / "a.md").write_text("mine")
    client = make_client()

    result = invoke(client, [str(audio)])

    assert result.exit_code == 1
    assert "exists (use --force" in result.output
    client.submit_job.assert_not_called()
    assert (tmp_path / "a.md").read_text() == "mine"


def test_force_overwrites(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    (tmp_path / "a.md").write_text("mine")
    (tmp_path / "a.result.json").write_text("{}")
    client = make_client("Neu")

    result = invoke(client, [str(audio), "--force"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "a.md").read_text().rstrip().endswith("Neu")
    assert json.loads((tmp_path / "a.result.json").read_text())["markdown"] == "Neu"


def test_unapproved_override_is_reported_clearly(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.submit_job.side_effect = ResonaAPIError(
        422, "unapproved_override", "engine/model override is not an approved candidate")

    result = invoke(client, [str(audio), "--engine", "deepgram"])

    assert result.exit_code == 1
    assert "unapproved_override" in result.output and "--engine/--model" in result.output


def test_admission_policy_denied_exits_2(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.submit_job.side_effect = PolicyDeniedError(403, "policy_denied", "asr: cloud disabled")

    result = invoke(client, [str(audio), "--allow-cloud"])

    assert result.exit_code == 2
    assert "policy_denied: asr: cloud disabled" in result.output


def test_job_failed_by_policy_exits_2(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.wait_for_job.side_effect = lambda job_id, **k: job_view(
        job_id, status="failed", error={"code": "policy_denied", "message": "llm: denied"})

    result = invoke(client, [str(audio)])

    assert result.exit_code == 2
    assert not (tmp_path / "a.md").exists()


def test_failed_job_reported_and_others_written(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.wav")
    client = make_client()
    client.wait_for_job.side_effect = lambda job_id, **k: (
        job_view(job_id, status="failed", error={"code": "unsupported_input", "message": "bad"})
        if job_id == 1 else job_view(job_id))

    result = invoke(client, [str(tmp_path)])

    assert result.exit_code == 1
    assert "job 1 failed: bad" in result.output
    assert not (tmp_path / "a.md").exists() and (tmp_path / "b.md").exists()


@pytest.mark.parametrize("error", [
    JobTimeout(1),
    httpx.ConnectError("hub down"),
    ResonaAPIError(500, "internal", "boom"),
])
def test_hub_errors_never_fall_back_to_local(tmp_path, hub, error):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.wait_for_job.side_effect = error

    with patch("resona_cli.local_run.run_local") as local_run:
        result = invoke(client, [str(audio)])

    assert result.exit_code == 1
    local_run.assert_not_called()
    assert not (tmp_path / "a.md").exists()


def test_timeout_names_the_running_job(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    client = make_client()
    client.wait_for_job.side_effect = JobTimeout(1)

    result = invoke(client, [str(audio), "--timeout", "5"])

    assert result.exit_code == 1
    assert "resona jobs wait 1" in result.output
    assert client.wait_for_job.call_args.kwargs["timeout"] == 5


def test_unreachable_hub_is_an_error_not_a_fallback(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    with (
        patch("resona_client.client.ResonaClient.from_config", side_effect=RuntimeError("no hub")),
        patch("resona_cli.local_run.run_local") as local_run,
    ):
        result = runner.invoke(app, ["transcribe", str(audio)])

    assert result.exit_code == 1
    assert "unreachable" in result.output
    local_run.assert_not_called()


def test_no_hub_configured_requires_explicit_local(tmp_path):
    audio = make_wav(tmp_path / "a.wav")
    with patch("resona_cli.local_run.run_local") as local_run:
        result = runner.invoke(app, ["transcribe", str(audio)])

    assert result.exit_code == 1
    assert "no hub configured" in result.output and "--local" in result.output
    local_run.assert_not_called()


def test_hub_rejects_profile_file(tmp_path, hub):
    audio = make_wav(tmp_path / "a.wav")
    profile = tmp_path / "p.json"
    profile.write_text('{"name": "p", "steps": []}')
    client = make_client()

    result = invoke(client, [str(audio), "--profile", str(profile)])

    assert result.exit_code == 1
    assert "only supported with --local" in result.output
    client.submit_job.assert_not_called()


def test_expands_directories_globs_and_deduplicates(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "b.mp3")
    (tmp_path / "notes.txt").write_text("x")
    client = make_client()

    result = invoke(client, [str(tmp_path), str(tmp_path / "a.wav"), str(tmp_path / "*.txt"),
                             str(tmp_path / "missing.wav")])

    assert result.exit_code == 0, result.output
    assert client.submit_job.call_count == 2
    assert "Not found" in result.output


def test_colliding_output_names_exit_2_before_any_submission(tmp_path, hub):
    """a.wav and a.mp3 would both write a.md/a.result.json: refuse the run up front."""
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "a.mp3")
    client = make_client()

    result = invoke(client, [str(tmp_path / "a.wav"), str(tmp_path / "a.mp3")])

    assert result.exit_code == 2
    assert "'a'" in result.output and "a.wav" in result.output and "a.mp3" in result.output
    client.submit_job.assert_not_called()
    assert not (tmp_path / "a.md").exists()


def test_colliding_output_dir_destinations_exit_2(tmp_path, hub):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        make_wav(tmp_path / name / "memo.wav")
    client = make_client()

    result = invoke(client, [str(tmp_path / "one" / "memo.wav"), str(tmp_path / "two" / "memo.wav"),
                             "--output-dir", str(tmp_path / "out")])

    assert result.exit_code == 2
    assert "'memo'" in result.output
    client.submit_job.assert_not_called()


def test_no_wait_writes_nothing_so_equal_stems_are_submitted(tmp_path, hub):
    make_wav(tmp_path / "a.wav")
    make_wav(tmp_path / "a.mp3")
    client = make_client()

    result = invoke(client, [str(tmp_path / "a.wav"), str(tmp_path / "a.mp3"), "--no-wait"])

    assert result.exit_code == 0, result.output
    assert client.submit_job.call_count == 2
