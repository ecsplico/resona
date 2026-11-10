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

# The OCR engine a document job runs with when neither its options nor its candidate pin one.
DEFAULT_OCR_ENGINE: OcrEngine = "ocrmac"
# Only RapidOCR exposes a recognition-confidence threshold (Docling ``RapidOcrOptions.text_score``).
_CONFIDENCE_OCR_ENGINES = frozenset({"rapidocr"})
# Languages the pinned rapidocr wheel (3.9.2) serves offline: all share its bundled multilingual
# PP-OCRv6 recognizer. Korean needs a PP-OCRv5 recognizer downloaded on first use, which a job never does.
_RAPIDOCR_LANGUAGES = frozenset({"de", "en", "fr", "it", "es"})

MAX_INSTRUCTIONS_CHARS = 8000
"""Upper bound (characters) for a job's user ``instructions`` to its postprocess profile's LLM steps."""

# Word documents (Office Open XML). Docling reads their XML directly: no pages are rendered and
# nothing is OCRed, so the options below have nothing to act on.
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PAGE_AND_OCR_OPTIONS = (
    "ocr", "ocr_engine", "page_start", "page_end", "image_scale", "ocr_min_confidence", "remove_repeated_headers",
)
# Values of those options that request nothing a DOCX would need: they stay allowed.
_DOCX_NO_OP = {"ocr": "off", "remove_repeated_headers": False}


class AudioOptions(BaseModel):
    """Typed audio job options; the hub forwards nothing else to the engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    translate: bool = False
    word_timestamps: bool = False
    vad_filter: bool = True


class TextOptions(BaseModel):
    """Text (postprocess-only) jobs take no options: the postprocess profile is the whole job."""

    model_config = ConfigDict(extra="forbid", frozen=True)


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
    remove_repeated_headers: bool | None = Field(
        default=None,
        description=(
            "Drop text blocks repeated in the top or bottom band of most pages (letterheads, patient header "
            "lines, fax headers, footers); the first occurrence stays."
        ),
    )

    @model_validator(mode="after")
    def _check_page_range(self) -> DocumentOptions:
        if self.page_start is not None and self.page_end is not None and self.page_start > self.page_end:
            raise ValueError("page_start must be <= page_end")
        return self

    @model_validator(mode="after")
    def _check_ocr_only_fields(self) -> DocumentOptions:
        # Both fields only act on OCR input/output; with OCR off they would be silently ignored.
        # image_scale 1.0 is the neutral raster, so it is not a conflict.
        if self.ocr == "off":
            if self.ocr_min_confidence is not None:
                raise ValueError("ocr_min_confidence requires OCR; ocr is 'off'")
            if self.image_scale not in (None, 1.0):
                raise ValueError("image_scale changes only the OCR raster; ocr is 'off'")
        return self


def document_options_conflict(
    options: DocumentOptions, ocr_engine: str, *, media_type: str | None = None,
) -> str | None:
    """Why ``options`` cannot run with ``ocr_engine`` on any host, or ``None`` if they can.

    Pure and dependency-free, so the hub filters candidates at admission with the same rule
    the Docling adapter enforces. Host-specific limits (platform, installed tesseract packs)
    are not covered: another worker may still serve those.

    With ``media_type`` :data:`DOCX_MEDIA_TYPE` the OCR engine plays no part: every explicitly
    set page or OCR option is a conflict (``ocr="off"`` and ``remove_repeated_headers=False``
    excepted, they describe what happens), so DOCX jobs fail at admission instead of silently
    ignoring them. Only fields in ``model_fields_set`` count, so the engine's resolved defaults
    are not mistaken for requests.
    """
    if media_type == DOCX_MEDIA_TYPE:
        refused = [
            name for name in _PAGE_AND_OCR_OPTIONS
            if name in options.model_fields_set
            and getattr(options, name) is not None
            and getattr(options, name) != _DOCX_NO_OP.get(name)
        ]
        if refused:
            return f"{', '.join(refused)} not supported for DOCX input (no pages, no OCR)"
        return None
    if options.ocr == "off":
        return None
    if options.ocr_min_confidence is not None and ocr_engine not in _CONFIDENCE_OCR_ENGINES:
        return (
            f"ocr_engine {ocr_engine!r} has no confidence threshold; ocr_min_confidence needs "
            f"{', '.join(sorted(_CONFIDENCE_OCR_ENGINES))}"
        )
    if ocr_engine == "rapidocr":
        unbundled = [lang for lang in options.languages or () if lang not in _RAPIDOCR_LANGUAGES]
        if unbundled:
            return f"ocr_engine 'rapidocr' has no bundled offline recognizer for {', '.join(unbundled)}"
    return None


__all__ = [
    "DEFAULT_OCR_ENGINE",
    "DOCX_MEDIA_TYPE",
    "AudioOptions",
    "DocumentOptions",
    "DocumentPipeline",
    "Language",
    "OcrEngine",
    "OcrMode",
    "document_options_conflict",
]
