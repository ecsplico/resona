"""Document language mapping from the job snapshot into DocumentOptions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import resona_engine_docling
from resona_engine_docling.convert import DocumentConversionFailed

from resona_worker.execute import ExitCode, main

DOCUMENT_CANDIDATE = {"engine": "docling", "model": "docling-project/docling", "accelerator": "cpu"}


def _run(tmp_path: Path, snapshot: dict) -> int:
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"kind": "document", "candidate": DOCUMENT_CANDIDATE, **snapshot}), encoding="utf-8")
    pdf = tmp_path / "input.bin"
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
    code = _run(tmp_path, {"language": "fr", "options": {}})

    assert code == ExitCode.UNSUPPORTED_INPUT
    assert captured_options == []
    assert not (tmp_path / "out.json").exists()
