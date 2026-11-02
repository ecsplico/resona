"""Typed job options: the wire contract shared by clients, the MCP server and engines.

Only these validated fields reach an engine; there is no pass-through of engine
keyword arguments, so job payloads cannot select arbitrary models or paths.
Document fields left unset are resolved by the execution profile/engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Document languages. Each OCR engine translates them to its own identifiers and rejects
# languages it cannot serve offline (e.g. Korean on the bundled RapidOCR recognizer).
Language = Literal["de", "en", "fr", "it", "es", "ko"]
# auto: OCR only bitmap regions, digital text is kept. force: full-page OCR. off: no OCR.
OcrMode = Literal["auto", "force", "off"]
OcrEngine = Literal["ocrmac", "rapidocr", "tesseract"]
DocumentPipeline = Literal["standard"]


class AudioOptions(BaseModel):
    """Typed audio job options; the hub forwards nothing else to the engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    translate: bool = False
    word_timestamps: bool = False
    vad_filter: bool = True


class DocumentOptions(BaseModel):
    """Typed document job options. Unset fields are resolved by the execution profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: list[Language] | None = Field(default=None, min_length=1)
    ocr: OcrMode | None = None
    ocr_engine: OcrEngine | None = None
    # Inclusive, 1-based page range. Either bound may be omitted.
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    pipeline: DocumentPipeline | None = None
    image_scale: float | None = Field(
        default=None, ge=1.0, le=4.0,
        description="Multiplier on the OCR raster resolution (1.0 = 216 dpi, 4.0 = 864 dpi). Requires OCR.",
    )
    ocr_min_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Drop OCR text below this recognizer confidence. Only engines with a threshold (rapidocr).",
    )

    @model_validator(mode="after")
    def _check_page_range(self) -> DocumentOptions:
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must be <= page_end")
        return self


__all__ = ["AudioOptions", "DocumentOptions", "DocumentPipeline", "Language", "OcrEngine", "OcrMode"]
