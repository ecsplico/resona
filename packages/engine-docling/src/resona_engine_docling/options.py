"""Document conversion options with the engine's resolved defaults.

The vocabulary, page-range validation and ``extra='forbid'`` come from the shared
wire contract :class:`resona_job_core.options.DocumentOptions`; this subclass only
replaces "unset" with the values the Docling adapter runs with.
"""

from __future__ import annotations

from pydantic import Field
from resona_job_core.options import DocumentOptions as WireDocumentOptions
from resona_job_core.options import DEFAULT_OCR_ENGINE, DocumentPipeline, Language, OcrEngine, OcrMode


class DocumentOptions(WireDocumentOptions):
    languages: list[Language] = Field(default_factory=lambda: ["de", "en"], min_length=1)
    ocr: OcrMode = "auto"
    ocr_engine: OcrEngine = DEFAULT_OCR_ENGINE
    pipeline: DocumentPipeline = "standard"
    # 1.0 keeps Docling's own OCR raster (216 dpi). ocr_min_confidence stays None: the engine's own threshold.
    image_scale: float = Field(default=1.0, ge=1.0, le=4.0)


__all__ = ["DocumentOptions", "Language", "OcrEngine", "OcrMode"]
