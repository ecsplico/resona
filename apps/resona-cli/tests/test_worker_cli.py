"""`resona worker ...` is resona-worker's CLI under the unified `resona` command."""
import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from resona_cli.main import app
from resona_job_core import Candidate
from resona_worker import cli as worker_cli
from resona_worker.config import CapabilitiesManifest, PreparedCapability

runner = CliRunner()


def test_worker_group_lists_every_worker_command():
    result = runner.invoke(app, ["worker", "--help"])

    assert result.exit_code == 0, result.output
    for command in ("run", "prepare", "capabilities", "install", "uninstall", "update"):
        assert command in result.output


def test_worker_prepare_runs_resona_workers_prepare(tmp_path, monkeypatch):
    candidate = Candidate(engine="faster-whisper", model="tiny", accelerator="cpu")
    calls = []

    def fake_prepare(settings, profile, **kwargs):
        calls.append((profile, kwargs["offline"], kwargs["model_pins"]))
        return CapabilitiesManifest(
            profile=profile, prepared_at=datetime.now(UTC), runtime_version="0.1.0", platform="test",
            tested=[PreparedCapability(kind="audio", candidate=candidate, seconds=1.0, peak_rss_bytes=1)],
        )

    monkeypatch.setattr(worker_cli, "prepare_profile", fake_prepare)
    out = tmp_path / "caps.json"

    result = runner.invoke(app, ["worker", "prepare", "--profile", "cpu-private", "--out", str(out), "--offline"])

    assert result.exit_code == 0, result.output
    assert calls == [("cpu-private", True, None)]
    assert json.loads(out.read_text())["profile"] == "cpu-private"
