"""Document conversion options with the engine's resolved defaults.

The vocabulary, page-range validation and ``extra='forbid'`` come from the shared
wire contract :class:`resona_job_core.options.DocumentOptions`; this subclass only
replaces "unset" with the values the Docling adapter runs with.
"""

from __future__ import annotations

from pydantic import Field
from resona_job_core.options import DocumentOptions as WireDocumentOptions
from resona_job_core.options import DocumentPipeline, Language, OcrEngine, OcrMode


class DocumentOptions(WireDocumentOptions):
    languages: list[Language] = Field(default_factory=lambda: ["de", "en"], min_length=1)
    ocr: OcrMode = "auto"
    ocr_engine: OcrEngine = "ocrmac"
    pipeline: DocumentPipeline = "standard"


__all__ = ["DocumentOptions", "Language", "OcrEngine", "OcrMode"]
