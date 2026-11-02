"""Document OCR/conversion adapter (PDF/image -> Markdown) backed by in-process Docling.

Deliberately NOT registered in the ``resona.engines`` entry-point group: that group
asserts the audio ``Transcriber`` protocol.
"""

from .convert import (
    ConflictingDocumentOptions,
    UnsupportedDocumentOptions,
    UnsupportedMediaType,
    convert_document,
    stage_weights,
)
from .options import DocumentOptions

__all__ = [
    "ConflictingDocumentOptions",
    "DocumentOptions",
    "UnsupportedDocumentOptions",
    "UnsupportedMediaType",
    "convert_document",
    "stage_weights",
]
