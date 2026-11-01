"""Shared job-option wire contract and the 'auto' provenance accelerator."""

import pytest
from pydantic import ValidationError
from resona_job_core import AudioOptions, DocumentOptions, StageProvenance


def test_document_options_leave_unset_fields_to_the_execution_profile() -> None:
    options = DocumentOptions(ocr="force", page_start=2, page_end=3)

    assert options.model_dump(exclude_none=True) == {"ocr": "force", "page_start": 2, "page_end": 3}


@pytest.mark.parametrize(
    "fields",
    [
        {"page_start": 3, "page_end": 2},
        {"page_start": 0},
        {"languages": []},
        {"languages": ["fr"]},
        {"ocr": "sometimes"},
        {"ocr_engine": "mistral"},
        {"pipeline": "vlm"},
        {"model_path": "/etc/passwd"},
    ],
)
def test_document_options_reject_values_outside_the_contract(fields: dict) -> None:
    with pytest.raises(ValidationError):
        DocumentOptions(**fields)


def test_audio_options_forbid_engine_keyword_forwarding() -> None:
    assert AudioOptions().model_dump() == {"translate": False, "word_timestamps": False, "vad_filter": True}
    with pytest.raises(ValidationError):
        AudioOptions(beam_size=5)


def test_stage_provenance_records_os_selected_device_as_auto() -> None:
    stage = StageProvenance(stage="ocr", engine="ocrmac", model="apple-vision", accelerator="auto", seconds=0.1)

    assert stage.accelerator == "auto"
    with pytest.raises(ValidationError):
        StageProvenance(stage="ocr", engine="ocrmac", model="apple-vision", accelerator="neural", seconds=0.1)
