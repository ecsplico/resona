"""Credential file, hub URL and capability file handling."""

from __future__ import annotations

import json
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


TINY = {"engine": "faster-whisper", "model": "tiny", "accelerator": "cpu"}


def _manifest(tested: list[dict]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "profile": "cpu-private",
            "prepared_at": "2026-10-31T12:00:00Z",
            "runtime_version": "0.1.0",
            "platform": "darwin-arm64",
            "tested": tested,
        }
    )


def test_load_capabilities_returns_only_tested_tuples_deduplicated(tmp_path: Path) -> None:
    caps = tmp_path / "caps.json"
    entry = {"kind": "audio", "candidate": TINY, "seconds": 1.0, "peak_rss_bytes": 1}
    caps.write_text(_manifest([entry, entry]), encoding="utf-8")

    assert load_capabilities(caps) == (Candidate(engine="faster-whisper", model="tiny", accelerator="cpu"),)


@pytest.mark.parametrize(
    "content",
    [
        json.dumps([TINY]),  # a bare list was never tested on this worker
        _manifest([]),
        _manifest([{"kind": "audio", "candidate": {**TINY, "accelerator": "tpu"}, "seconds": 1, "peak_rss_bytes": 1}]),
    ],
)
def test_load_capabilities_rejects_untested_or_malformed_files(tmp_path: Path, content: str) -> None:
    caps = tmp_path / "caps.json"
    caps.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="prepare"):
        load_capabilities(caps)
