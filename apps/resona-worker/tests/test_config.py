"""Credential file, hub URL and capability file handling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from resona_job_core import Candidate

from resona_worker.config import (
    ConfigError,
    WorkerConfig,
    load_capabilities,
    load_token,
    validate_hub_url,
)


def _token_file(tmp_path: Path, content: str = "wkr-secret-value\n", mode: int = 0o600) -> Path:
    path = tmp_path / "worker.token"
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_load_token_reads_0600_regular_file_and_strips_whitespace(tmp_path: Path) -> None:
    assert load_token(_token_file(tmp_path)) == "wkr-secret-value"


def test_load_token_refuses_group_or_world_readable_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_token(_token_file(tmp_path, mode=0o640))
    assert "wkr-secret-value" not in str(info.value)


def test_load_token_refuses_symlink(tmp_path: Path) -> None:
    target = _token_file(tmp_path)
    link = tmp_path / "link.token"
    link.symlink_to(target)
    with pytest.raises(ConfigError):
        load_token(link)


def test_load_token_refuses_empty_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_token(_token_file(tmp_path, content="  \n"))


@pytest.mark.parametrize(
    "url",
    ["https://hub.example.de/", "http://127.0.0.1:7000", "http://localhost:7000"],
)
def test_hub_url_accepts_https_and_loopback_http(url: str) -> None:
    validate_hub_url(url)


@pytest.mark.parametrize("url", ["http://hub.example.de", "ftp://hub.example.de", "https://"])
def test_hub_url_rejects_plaintext_remote_and_other_schemes(url: str) -> None:
    with pytest.raises(ConfigError):
        validate_hub_url(url)


def test_worker_config_never_exposes_the_token_in_repr(tmp_path: Path) -> None:
    config = WorkerConfig(
        hub_url="https://hub.example.de",
        token="wkr-do-not-print",
        work_dir=tmp_path,
        capabilities=(Candidate(engine="faster-whisper", model="tiny", accelerator="cpu"),),
    )
    assert "wkr-do-not-print" not in repr(config)


def test_load_capabilities_dedupes_and_rejects_malformed_entries(tmp_path: Path) -> None:
    good = tmp_path / "caps.json"
    good.write_text(
        '[{"engine":"faster-whisper","model":"tiny","accelerator":"cpu"},'
        '{"engine":"faster-whisper","model":"tiny","accelerator":"cpu"}]',
        encoding="utf-8",
    )
    assert load_capabilities(good) == (
        Candidate(engine="faster-whisper", model="tiny", accelerator="cpu"),
    )

    bad = tmp_path / "bad.json"
    bad.write_text('[{"engine":"faster-whisper","accelerator":"tpu"}]', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_capabilities(bad)
