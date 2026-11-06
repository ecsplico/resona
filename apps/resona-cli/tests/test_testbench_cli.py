"""`resona testbench add|list|status`."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import ResonaAPIError
from resona_client.config import EngineConfig

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hub(monkeypatch):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def _run(client, *args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, ["testbench", *args])


def test_add_contributes_each_file_in_directory(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"a")
    (tmp_path / "b.wav").write_bytes(b"b")
    client = MagicMock()
    client.testbench_contribute.side_effect = [
        {"id": 1, "status": "awaiting_draft", "kind": "audio"},
        {"id": 2, "status": "awaiting_draft", "kind": "audio"},
    ]

    result = _run(client, "add", str(tmp_path), "--language", "de", "--label", "x")

    assert result.exit_code == 0, result.output
    assert client.testbench_contribute.call_count == 2
    assert client.testbench_contribute.call_args.kwargs == {"language": "de", "label": "x"}
    assert "awaiting_draft" in result.stdout


def test_add_json(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"a")
    client = MagicMock()
    client.testbench_contribute.return_value = {"id": 7, "status": "awaiting_draft", "kind": "audio"}

    result = _run(client, "add", str(f), "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["id"] == 7


def test_add_disabled_hints_settings(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"a")
    client = MagicMock()
    client.testbench_contribute.side_effect = ResonaAPIError(403, "testbench_disabled", "off")

    result = _run(client, "add", str(f))

    assert result.exit_code == 1
    assert "testbench_enabled" in result.output and "resona settings set --file" in result.output


def test_add_partial_failure_exits_1(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"a")
    client = MagicMock()
    client.testbench_contribute.side_effect = ResonaAPIError(415, "unsupported_media", "nope")

    result = _run(client, "add", str(f))

    assert result.exit_code == 1
    assert "unsupported_media" in result.output


def test_list_filters_and_prints():
    client = MagicMock()
    client.testbench_list_items.return_value = {
        "items": [{"id": 3, "kind": "audio", "status": "corrected", "run_count": 4, "label": "memo"}],
        "next_cursor": None,
    }

    result = _run(client, "list", "--status", "corrected", "--kind", "audio")

    assert result.exit_code == 0, result.output
    assert client.testbench_list_items.call_args.kwargs["status"] == "corrected"
    assert "3\taudio\tcorrected\truns=4\tmemo" in result.stdout


def test_status_disabled():
    client = MagicMock()
    client.testbench_status.return_value = {"enabled": False, "items": 0, "corrected": 0, "runs": 0}

    result = _run(client, "status")

    assert result.exit_code == 0, result.output
    assert "enabled: false" in result.stdout
