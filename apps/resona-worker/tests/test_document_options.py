"""Document language mapping from the job snapshot into DocumentOptions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import resona_engine_docling
from resona_engine_docling.convert import (
    ConflictingDocumentOptions,
    DocumentConversionFailed,
    UnsupportedDocumentOptions,
)

from resona_worker.execute import ExitCode, main

DOCUMENT_CANDIDATE = {"engine": "docling", "model": "docling-project/docling", "accelerator": "cpu"}


def _run(tmp_path: Path, snapshot: dict, *, input_name: str = "input.bin", candidate: dict = DOCUMENT_CANDIDATE) -> int:
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"kind": "document", "candidate": candidate, **snapshot}), encoding="utf-8")
    pdf = tmp_path / input_name
    pdf.write_bytes(b"%PDF-1.4 not really")
    return main(["--snapshot", str(snap), "--input", str(pdf), "--out", str(tmp_path / "out.json")])


@pytest.fixture
def captured_options(monkeypatch) -> list:
    seen: list = []

    def fake_convert(path, *, options, accelerator):
        seen.append(options)
        raise DocumentConversionFailed("stop after capturing options")

    monkeypatch.setattr(resona_engine_docling, "convert_document", fake_convert)
    return seen


@pytest.mark.parametrize("language", ["de", "en"])
def test_snapshot_language_becomes_document_languages(tmp_path: Path, captured_options: list, language: str) -> None:
    code = _run(tmp_path, {"language": language, "options": {}})

    assert code == ExitCode.UNSUPPORTED_INPUT
    assert [options.languages for options in captured_options] == [[language]]


def test_explicit_option_languages_win_over_snapshot_language(tmp_path: Path, captured_options: list) -> None:
    _run(tmp_path, {"language": "de", "options": {"languages": ["en"]}})

    assert [options.languages for options in captured_options] == [["en"]]


def test_unsupported_snapshot_language_fails_without_converting(tmp_path: Path, captured_options: list) -> None:
    code = _run(tmp_path, {"language": "nl", "options": {}})

    assert code == ExitCode.UNSUPPORTED_INPUT
    assert captured_options == []
    assert not (tmp_path / "out.json").exists()


def test_docx_input_does_not_inherit_the_candidates_ocr_engine(tmp_path: Path, captured_options: list) -> None:
    # The engine refuses explicitly set OCR options for DOCX; the candidate's OCR engine must not count as one.
    candidate = {**DOCUMENT_CANDIDATE, "ocr_engine": "rapidocr"}

    _run(tmp_path, {"language": "de", "options": {}}, input_name="input.docx", candidate=candidate)
    _run(tmp_path, {"language": "de", "options": {}}, input_name="input.pdf", candidate=candidate)

    docx, pdf = captured_options
    assert "ocr_engine" not in docx.model_fields_set
    assert pdf.ocr_engine == "rapidocr"


@pytest.mark.parametrize(
    "error, expected",
    [
        # Options no worker can run fail the job; a host gap (e.g. missing tesseract pack) withdraws the capability.
        (ConflictingDocumentOptions("ocr_engine 'ocrmac' has no confidence threshold"), ExitCode.INVALID_SNAPSHOT),
        (UnsupportedDocumentOptions("tesseract language pack 'fra' is not installed"), ExitCode.UNSUPPORTED_CAPABILITY),
    ],
)
def test_conflicting_options_fail_the_job_while_host_gaps_are_capability_errors(
    tmp_path: Path, monkeypatch, error: Exception, expected: ExitCode
) -> None:
    seen: list = []

    def fake_convert(path, *, options, accelerator):
        seen.append(options)
        raise error

    monkeypatch.setattr(resona_engine_docling, "convert_document", fake_convert)

    code = _run(tmp_path, {"language": "fr", "options": {"image_scale": 2.0, "ocr_min_confidence": 0.6}})

    assert code == expected
    assert [(o.languages, o.image_scale, o.ocr_min_confidence) for o in seen] == [(["fr"], 2.0, 0.6)]
