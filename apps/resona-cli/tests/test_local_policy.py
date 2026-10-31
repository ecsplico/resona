"""Explicit `--local`: same policy path as the hub, cached settings, execution subprocess."""
import io
import json
import struct
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import resona_postprocess.llm as llm_mod
from resona_cli import settings_cache
from resona_cli.main import app
from resona_client.client import SettingsDoc
from resona_client.config import EngineConfig
from resona_job_core import JobResult, default_settings

runner = CliRunner()

CLOUD_PROFILE = {
    "name": "cloud-polish",
    "steps": [{"type": "llm", "name": "polish", "prompt": "fix", "model": "gpt-4o-mini"}],
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr(settings_cache, "CACHE_DIR", tmp_path / "settings-cache")
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")
    monkeypatch.setenv("RESONA_API_KEY", "client-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")


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


def _cache_settings(**changes) -> Path:
    settings = default_settings().model_copy(update=changes).model_dump(mode="json")
    doc = SettingsDoc(organization_id="org-a", revision=7, etag='"7"', settings=settings)
    return settings_cache.store(doc, hub_url="http://hub.test", api_key="client-key").path


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


class _FakeExecution:
    """Stands in for the execution subprocess: records the snapshot/env, writes a result."""

    def __init__(self, returncode=0, text="raw text"):
        self.calls: list[dict] = []
        self.returncode = returncode
        self.text = text

    def __call__(self, argv, *, env, **kwargs):
        args = dict(zip(argv[3::2], argv[4::2]))
        snapshot = json.loads(Path(args["--snapshot"]).read_text())
        self.calls.append({"argv": argv, "env": env, "snapshot": snapshot})
        if self.returncode == 0:
            stage = {"stage": "asr" if snapshot["kind"] == "audio" else "layout",
                     "engine": snapshot["candidate"]["engine"], "model": snapshot["candidate"]["model"],
                     "accelerator": snapshot["candidate"]["accelerator"], "seconds": 0.1}
            result = JobResult(kind=snapshot["kind"], raw_text=self.text, markdown=self.text,
                               language=snapshot["language"], provenance={"stages": [stage]})
            Path(args["--out"]).write_text(result.model_dump_json())
        return subprocess.CompletedProcess(argv, self.returncode)


@pytest.fixture
def execution(monkeypatch):
    fake = _FakeExecution()
    monkeypatch.setattr("resona_cli.local_run.subprocess.run", fake)
    return fake


def test_local_default_is_private_and_denies_cloud_llm_before_any_call(tmp_path, litellm_spy, execution):
    audio = _make_wav(tmp_path / "audio.wav")

    result = runner.invoke(app, ["transcribe", str(audio), "--local", "--execution-profile",
                                 "cpu-private", "--profile", str(_profile_file(tmp_path))])

    assert result.exit_code == 2
    assert "policy_denied: polish:" in result.output
    assert litellm_spy.calls == [] and execution.calls == []


def test_local_allow_cloud_without_org_cloud_enablement_is_denied(tmp_path, litellm_spy, execution):
    audio = _make_wav(tmp_path / "audio.wav")

    result = runner.invoke(app, ["transcribe", str(audio), "--local", "--allow-cloud",
                                 "--execution-profile", "cpu-private",
                                 "--profile", str(_profile_file(tmp_path))])

    assert result.exit_code == 2
    assert "cloud processing is disabled" in result.output
    assert litellm_spy.calls == [] and execution.calls == []


def test_local_allow_cloud_with_cached_org_cloud_enabled_runs_llm(tmp_path, litellm_spy, execution):
    audio = _make_wav(tmp_path / "audio.wav")
    _cache_settings(cloud_enabled=True)

    result = runner.invoke(app, ["transcribe", str(audio), "--local", "--allow-cloud",
                                 "--execution-profile", "cpu-private",
                                 "--profile", str(_profile_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert [c["model"] for c in litellm_spy.calls] == ["gpt-4o-mini"]
    stored = JobResult.model_validate_json((tmp_path / "audio.result.json").read_text())
    assert stored.raw_text == "raw text" and stored.markdown == "FORMATTED"
    assert stored.provenance.worker_id == "local" and stored.provenance.settings_revision == 7


def test_local_runs_cached_candidate_in_private_subprocess_and_marks_cache_stale(tmp_path, execution):
    audio = _make_wav(tmp_path / "audio.wav")
    cache = _cache_settings(language="en", default_execution_profile="cpu-private")

    result = runner.invoke(app, ["transcribe", str(audio), "--local"])

    assert result.exit_code == 0, result.output
    [call] = execution.calls
    assert call["argv"][1:3] == ["-m", "resona_worker.execute"]
    assert call["snapshot"]["candidate"]["engine"] == "faster-whisper"
    assert call["snapshot"]["language"] == "en"
    assert "OPENAI_API_KEY" not in call["env"] and "RESONA_API_KEY" not in call["env"]
    assert call["env"]["HF_HUB_OFFLINE"] == "1"
    assert json.loads(cache.read_text())["stale"] is True
    assert "revision 7" in result.output and "stale" in result.output


def test_local_rejects_unapproved_override(tmp_path, execution):
    audio = _make_wav(tmp_path / "audio.wav")

    result = runner.invoke(app, ["transcribe", str(audio), "--local", "--execution-profile",
                                 "cpu-private", "--engine", "deepgram"])

    assert result.exit_code == 1
    assert "unapproved_override" in result.output
    assert execution.calls == []


def test_local_execution_failure_is_reported_without_output(tmp_path, monkeypatch):
    audio = _make_wav(tmp_path / "audio.wav")
    monkeypatch.setattr("resona_cli.local_run.subprocess.run", _FakeExecution(returncode=4))

    result = runner.invoke(app, ["transcribe", str(audio), "--local", "--execution-profile", "cpu-private"])

    assert result.exit_code == 1
    assert "unsupported_capability" in result.output
    assert not (tmp_path / "audio.md").exists()


def test_local_never_contacts_the_hub(tmp_path, execution):
    audio = _make_wav(tmp_path / "audio.wav")
    with patch("resona_client.client.ResonaClient.from_config") as from_config:
        result = runner.invoke(app, ["transcribe", str(audio), "--local", "--execution-profile", "cpu-private"])

    assert result.exit_code == 0, result.output
    from_config.assert_not_called()


def test_doc_local_passes_document_options(tmp_path, execution):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    result = runner.invoke(app, ["doc", str(pdf), "--local", "--execution-profile", "cpu-private",
                                 "--ocr", "force", "--languages", "de", "--pages", "2-3"])

    assert result.exit_code == 0, result.output
    [call] = execution.calls
    assert call["snapshot"]["kind"] == "document"
    assert call["snapshot"]["candidate"]["ocr_engine"] == "rapidocr"
    assert call["snapshot"]["options"] == {"ocr": "force", "languages": ["de"], "page_start": 2, "page_end": 3}
    assert (tmp_path / "scan.md").exists() and (tmp_path / "scan.result.json").exists()


def test_watch_local_denies_cloud_llm_before_watching(tmp_path, litellm_spy, execution):
    profile = _profile_file(tmp_path)
    _make_wav(tmp_path / "audio.wav")

    with patch("resona_cli.watch.time.sleep", side_effect=AssertionError("must not loop")):
        result = runner.invoke(app, ["watch", str(tmp_path), "--local", "--execution-profile",
                                     "cpu-private", "--profile", str(profile)])

    assert result.exit_code == 2
    assert "policy_denied: polish:" in result.output
    assert litellm_spy.calls == [] and execution.calls == []
