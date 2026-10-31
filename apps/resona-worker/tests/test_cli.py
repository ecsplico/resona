"""CLI refuses unusable credentials before any hub contact, without echoing the token."""

from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner
from resona_worker import cli

runner = CliRunner()


def test_run_rejects_group_readable_token_without_contacting_hub(tmp_path: Path, monkeypatch) -> None:
    token = tmp_path / "worker.token"
    token.write_text("wkr-cli-secret\n", encoding="utf-8")
    os.chmod(token, 0o644)
    caps = tmp_path / "caps.json"
    caps.write_text('[{"engine":"faster-whisper","model":"tiny","accelerator":"cpu"}]', encoding="utf-8")

    def _no_supervisor(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("supervisor must not start with an unusable credential")

    monkeypatch.setattr(cli, "Supervisor", _no_supervisor)

    result = runner.invoke(
        cli.app,
        [
            "run",
            "--hub", "https://hub.example.de",
            "--token-file", str(token),
            "--capabilities-file", str(caps),
            "--work-dir", str(tmp_path / "work"),
            "--once",
        ],
    )

    assert result.exit_code == 2
    assert "configuration error" in result.output
    assert "wkr-cli-secret" not in result.output
