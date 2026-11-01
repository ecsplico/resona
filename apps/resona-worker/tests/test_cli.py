"""CLI: refuses unusable credentials/capabilities before any hub contact; prepare/capabilities commands."""

from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path

from resona_job_core import Candidate, default_settings
from typer.testing import CliRunner

from resona_worker import cli
from resona_worker.prepare import prepare

runner = CliRunner()
TINY = Candidate(engine="faster-whisper", model="tiny", accelerator="cpu")


def _no_supervisor(*args, **kwargs):  # pragma: no cover - must not be reached
    raise AssertionError("supervisor must not start with unusable configuration")


def _run_args(tmp_path: Path, token: Path, caps: Path) -> list[str]:
    return [
        "run",
        "--hub", "https://hub.example.de",
        "--token-file", str(token),
        "--capabilities-file", str(caps),
        "--work-dir", str(tmp_path / "work"),
        "--once",
    ]


def test_run_rejects_group_readable_token_without_contacting_hub(tmp_path: Path, monkeypatch, fakes) -> None:
    token = tmp_path / "worker.token"
    token.write_text("wkr-cli-secret\n", encoding="utf-8")
    os.chmod(token, 0o644)
    caps = tmp_path / "caps.json"
    caps.write_text(fakes.manifest_json([TINY]), encoding="utf-8")
    monkeypatch.setattr(cli, "Supervisor", _no_supervisor)

    result = runner.invoke(cli.app, _run_args(tmp_path, token, caps))

    assert result.exit_code == 2
    assert "configuration error" in result.output
    assert "wkr-cli-secret" not in result.output


def test_run_refuses_an_unprepared_capability_list(tmp_path: Path, monkeypatch) -> None:
    token = tmp_path / "worker.token"
    token.write_text("wkr-cli-secret\n", encoding="utf-8")
    os.chmod(token, 0o600)
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps([TINY.model_dump(mode="json")]), encoding="utf-8")
    monkeypatch.setattr(cli, "Supervisor", _no_supervisor)

    result = runner.invoke(cli.app, _run_args(tmp_path, token, caps))

    assert result.exit_code == 2
    assert "resona-worker prepare" in result.output


def test_capabilities_prints_the_tested_tuples_as_an_approval_list(tmp_path: Path, fakes) -> None:
    caps = tmp_path / "caps.json"
    caps.write_text(fakes.manifest_json([TINY]), encoding="utf-8")

    result = runner.invoke(cli.app, ["capabilities", str(caps)])

    assert result.exit_code == 0
    assert [Candidate.model_validate(item) for item in json.loads(result.stdout)] == [TINY]


def test_prepare_reads_the_hub_settings_envelope_and_writes_the_manifest(
    tmp_path: Path, monkeypatch, prepare_executor: Path
) -> None:
    settings = default_settings().model_dump(mode="json")
    settings["execution_profiles"]["tiny-cpu"] = {"audio": [TINY.model_dump(mode="json")], "document": []}
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"organization_id": "org", "revision": 4, "settings": settings}))
    monkeypatch.setattr(cli, "prepare_profile", functools.partial(prepare, executor=(sys.executable, str(prepare_executor))))
    out = tmp_path / "caps.json"

    result = runner.invoke(
        cli.app, ["prepare", "--profile", "tiny-cpu", "--settings-file", str(settings_file), "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads(out.read_text())
    assert manifest["profile"] == "tiny-cpu"
    assert [Candidate.model_validate(t["candidate"]) for t in manifest["tested"]] == [TINY]


def test_failed_prepare_keeps_the_previous_manifest(tmp_path: Path, monkeypatch, prepare_executor: Path) -> None:
    out = tmp_path / "caps.json"
    out.write_text("previous working manifest", encoding="utf-8")
    monkeypatch.setattr(cli, "prepare_profile", functools.partial(prepare, executor=(sys.executable, str(prepare_executor))))

    result = runner.invoke(cli.app, ["prepare", "--profile", "no-such-profile", "--out", str(out)])

    assert result.exit_code == 1
    assert "prepare failed" in result.output
    assert out.read_text() == "previous working manifest"


def test_prepare_with_a_release_manifest_whose_layout_pin_differs_fails_offline(
    tmp_path: Path, monkeypatch, hf_cache
) -> None:
    from resona_job_core.release import ReleaseManifest

    from resona_worker import prepare as prepare_module

    layout, other = "docling-project/docling-layout-heron", "1" * 40
    hf_cache.add(layout, "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8", "main")
    monkeypatch.setattr(prepare_module, "_stage_weights", lambda kind: [(layout, "main")] if kind == "document" else [])
    manifest = ReleaseManifest.model_validate({
        "release_id": "0.2.0", "created_at": "2026-11-01T00:00:00Z",
        "wheels": [{"name": "resona-worker", "version": "0.2.0", "sha256": "a" * 64}],
        "models": [{"engine": "docling-layout", "model": layout, "revision": other}],
    })
    manifest_file = tmp_path / "release-manifest.json"
    manifest_file.write_text(manifest.model_dump_json(), encoding="utf-8")
    settings = {"execution_profiles": {"docs": {"audio": [], "document": [
        {"engine": "docling", "model": "docling-standard", "accelerator": "cpu", "ocr_engine": "rapidocr"},
    ]}}}
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    out = tmp_path / "caps.json"

    result = runner.invoke(cli.app, [
        "prepare", "--profile", "docs", "--settings-file", str(settings_file), "--out", str(out),
        "--release-manifest", str(manifest_file), "--offline",
    ])

    assert result.exit_code == 1
    assert f"prepare failed: {layout}@{other} is not in the local HuggingFace cache and --offline" in result.output
    assert not out.exists()
