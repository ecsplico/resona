"""Shared job-option wire contract and the 'auto' provenance accelerator."""

import pytest
from pydantic import ValidationError
from resona_job_core import AudioOptions, DocumentOptions, StageProvenance, document_options_conflict


def test_document_options_leave_unset_fields_to_the_execution_profile() -> None:
    options = DocumentOptions(ocr="force", page_start=2, page_end=3)

    assert options.model_dump(exclude_none=True) == {"ocr": "force", "page_start": 2, "page_end": 3}


@pytest.mark.parametrize(
    "fields",
    [
        {"page_start": 3, "page_end": 2},
        {"page_start": 0},
        {"languages": []},
        {"languages": ["nl"]},
        {"image_scale": 0.5},
        {"image_scale": 4.5},
        {"ocr_min_confidence": 1.5},
        {"ocr": "sometimes"},
        {"ocr": "off", "image_scale": 2.0},
        {"ocr": "off", "ocr_min_confidence": 0.6},
        {"ocr_engine": "mistral"},
        {"pipeline": "vlm"},
        {"model_path": "/etc/passwd"},
    ],
)
def test_document_options_reject_values_outside_the_contract(fields: dict) -> None:
    with pytest.raises(ValidationError):
        DocumentOptions(**fields)


def test_document_options_with_ocr_off_accept_the_neutral_image_scale() -> None:
    assert DocumentOptions(ocr="off", image_scale=1.0).ocr == "off"


@pytest.mark.parametrize(
    "fields, ocr_engine",
    [
        ({"ocr_min_confidence": 0.6}, "ocrmac"),
        ({"ocr_min_confidence": 0.6}, "tesseract"),
        ({"languages": ["de", "ko"]}, "rapidocr"),
    ],
)
def test_document_options_conflict_with_an_ocr_engine_that_cannot_run_them(fields: dict, ocr_engine: str) -> None:
    assert document_options_conflict(DocumentOptions(**fields), ocr_engine) is not None


@pytest.mark.parametrize(
    "fields, ocr_engine",
    [
        ({"ocr_min_confidence": 0.6, "languages": ["de", "en"]}, "rapidocr"),
        ({"languages": ["ko"]}, "ocrmac"),
        ({"languages": ["ko"]}, "tesseract"),
        ({"ocr": "off", "languages": ["ko"]}, "rapidocr"),
    ],
)
def test_document_options_compatible_with_an_ocr_engine_have_no_conflict(fields: dict, ocr_engine: str) -> None:
    assert document_options_conflict(DocumentOptions(**fields), ocr_engine) is None


def test_audio_options_forbid_engine_keyword_forwarding() -> None:
    assert AudioOptions().model_dump() == {
        "translate": False, "word_timestamps": False, "vad_filter": True, "diarize": False
    }
    with pytest.raises(ValidationError):
        AudioOptions(beam_size=5)


def test_stage_provenance_records_os_selected_device_as_auto() -> None:
    stage = StageProvenance(stage="ocr", engine="ocrmac", model="apple-vision", accelerator="auto", seconds=0.1)

    assert stage.accelerator == "auto"
    with pytest.raises(ValidationError):
        StageProvenance(stage="ocr", engine="ocrmac", model="apple-vision", accelerator="neural", seconds=0.1)
