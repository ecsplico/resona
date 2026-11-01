"""Document OCR/conversion adapter (PDF/image -> Markdown) backed by in-process Docling.

Deliberately NOT registered in the ``resona.engines`` entry-point group: that group
asserts the audio ``Transcriber`` protocol.
"""

from .convert import UnsupportedDocumentOptions, UnsupportedMediaType, convert_document, stage_weights
from .options import DocumentOptions

__all__ = [
    "DocumentOptions",
    "UnsupportedDocumentOptions",
    "UnsupportedMediaType",
    "convert_document",
    "stage_weights",
]
