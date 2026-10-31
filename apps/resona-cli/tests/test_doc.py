"""`resona doc`: document jobs with typed DocumentOptions."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app
from resona_client.client import DocumentOptions, JobRef, JobView
from resona_client.config import EngineConfig
from resona_job_core import JobResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _hub(monkeypatch, tmp_path):
    monkeypatch.setattr(EngineConfig, "load", classmethod(lambda cls: cls()))
    monkeypatch.setattr("resona_cli.settings_cache.CACHE_DIR", tmp_path / "settings-cache")
    monkeypatch.setenv("RESONA_API_URL", "http://hub.test")


def _client():
    client = MagicMock()
    client.submit_job.return_value = JobRef(job_id=5, status="pending")
    client.wait_for_job.return_value = JobView.from_dict({
        "job_id": 5, "kind": "document", "status": "completed", "private": True,
        "execution_profile": "cpu-private",
        "candidate": {"engine": "docling", "model": "docling-standard", "accelerator": "cpu",
                      "ocr_engine": "rapidocr"}})
    client.get_result.return_value = JobResult(
        kind="document", raw_text="Seite eins", markdown="# Seite eins", language="de",
        pages=[{"number": 1, "text": "Seite eins"}],
        provenance={"stages": [{"stage": "layout", "engine": "docling", "model": "docling-standard",
                                "accelerator": "cpu", "seconds": 1.0}]})
    return client


def _invoke(client, args):
    with patch("resona_client.client.ResonaClient.from_config", return_value=client):
        return runner.invoke(app, ["doc", *args])


def test_doc_submits_document_job_and_writes_outputs(tmp_path):
    pdf = tmp_path / "brief.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    client = _client()

    result = _invoke(client, [str(pdf), "--execution-profile", "cpu-private", "--ocr", "force",
                              "--ocr-engine", "rapidocr", "--languages", "de,en", "--pages", "2-4"])

    assert result.exit_code == 0, result.output
    kwargs = client.submit_job.call_args.kwargs
    assert kwargs["kind"] == "document" and kwargs["private"] is True
    assert kwargs["options"] == DocumentOptions(ocr="force", ocr_engine="rapidocr",
                                                languages=["de", "en"], page_start=2, page_end=4)
    assert (tmp_path / "brief.md").read_text().rstrip().endswith("# Seite eins")
    stored = json.loads((tmp_path / "brief.result.json").read_text())
    assert stored["raw_text"] == "Seite eins" and stored["pages"][0]["number"] == 1
    assert "candidate=docling/docling-standard@cpu+rapidocr" in result.output


@pytest.mark.parametrize("args, message", [
    (["--ocr", "sometimes"], "invalid document options"),
    (["--languages", "fr"], "invalid document options"),
    (["--pages", "5-2"], "invalid document options"),
    (["--pages", "x"], "invalid --pages"),
])
def test_doc_rejects_invalid_options_before_submitting(tmp_path, args, message):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    client = _client()

    result = _invoke(client, [str(pdf), *args])

    assert result.exit_code == 1
    assert message in result.output
    client.submit_job.assert_not_called()


def test_doc_single_page(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    client = _client()

    result = _invoke(client, [str(pdf), "--pages", "3"])

    assert result.exit_code == 0, result.output
    options = client.submit_job.call_args.kwargs["options"]
    assert (options.page_start, options.page_end) == (3, 3)


def test_doc_directory_picks_documents_only(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "c.wav").write_bytes(b"RIFF")
    client = _client()
    client.submit_job.side_effect = [JobRef(job_id=5, status="pending"), JobRef(job_id=6, status="pending")]

    result = _invoke(client, [str(tmp_path), "--no-wait"])

    assert result.exit_code == 0, result.output
    assert [c.args[0].name for c in client.submit_job.call_args_list] == ["a.pdf", "b.png"]
