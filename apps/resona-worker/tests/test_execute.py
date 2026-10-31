"""Execution subprocess environment and exit-code contract."""

from __future__ import annotations

import json
from pathlib import Path

from resona_worker.execute import ExitCode, main, subprocess_env

BASE_ENV = {
    "PATH": "/usr/bin",
    "OPENAI_API_KEY": "sk-openai",
    "DEEPGRAM_API_KEY": "dg-key",
    "MY_PROVIDER_API_KEY": "custom-key",
    "HOME": "/Users/worker",
}


def test_private_environment_drops_provider_keys_and_forces_offline_mode() -> None:
    env = subprocess_env(private=True, base=BASE_ENV)

    assert "OPENAI_API_KEY" not in env
    assert "DEEPGRAM_API_KEY" not in env
    assert "MY_PROVIDER_API_KEY" not in env
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_non_private_environment_keeps_inherited_settings() -> None:
    env = subprocess_env(private=False, base=BASE_ENV)

    assert env["OPENAI_API_KEY"] == "sk-openai"
    assert "HF_HUB_OFFLINE" not in env


def test_executor_rejects_unknown_job_kind_with_invalid_snapshot_exit(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"kind": "video", "candidate": {"engine": "x", "model": "y", "accelerator": "cpu"}}),
        encoding="utf-8",
    )
    audio = tmp_path / "input.bin"
    audio.write_bytes(b"anything")

    code = main(["--snapshot", str(snapshot), "--input", str(audio), "--out", str(tmp_path / "out.json")])

    assert code == ExitCode.INVALID_SNAPSHOT
    assert not (tmp_path / "out.json").exists()
