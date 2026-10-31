"""Caller-facing document conversion options.

Only typed, validated fields are accepted; there is no pass-through of Docling
keyword arguments, so job payloads cannot select arbitrary models or paths.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["de", "en"]
OcrMode = Literal["auto", "force", "off"]
OcrEngine = Literal["ocrmac", "rapidocr", "tesseract"]


class DocumentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: list[Language] = Field(default_factory=lambda: ["de", "en"], min_length=1)
    # auto: OCR only bitmap regions, digital text is kept. force: full-page OCR. off: no OCR.
    ocr: OcrMode = "auto"
    ocr_engine: OcrEngine = "ocrmac"
    # Inclusive, 1-based page range. Either bound may be omitted.
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    pipeline: Literal["standard"] = "standard"

    @model_validator(mode="after")
    def _check_page_range(self) -> DocumentOptions:
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must be <= page_end")
        return self


__all__ = ["DocumentOptions", "Language", "OcrEngine", "OcrMode"]
