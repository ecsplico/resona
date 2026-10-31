"""In-process Docling document conversion (PDF/image -> Markdown + page structure).

Runs ``DocumentConverter`` directly; there is no CLI subprocess and no docling-serve
endpoint. Options are typed (:class:`DocumentOptions`) and translated here into the
exact Docling 2.129 option classes; nothing is forwarded verbatim.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.settings import settings
from docling.datamodel.pipeline_options import (
    OcrMacOptions,
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
from docling_core.types.doc import FormulaItem, PictureItem, TableItem, TextItem
from docling_core.types.doc.document import DoclingDocument
from resona_job_core import BBox, JobResult, Page, PageBlock, Provenance, StageProvenance

from .options import DocumentOptions

# Docling records per-stage timings only when this flag is set; they feed provenance.seconds.
settings.debug.profile_pipeline_timings = True

Accelerator = Literal["cpu", "cuda", "metal"]

PDF_SUFFIXES = frozenset({".pdf"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
FORMULA_PLACEHOLDER = "<!-- formula-not-decoded -->"
IMAGE_PLACEHOLDER = "<!-- image -->"

# Docling language identifiers differ per OCR backend; Resona exposes de/en only.
_OCRMAC_LANG = {"de": "de-DE", "en": "en-US"}
_TESSERACT_LANG = {"de": "deu", "en": "eng"}
# RapidOCR recognizer family per language (rapidocr's PP-OCRv6 route). Languages in one family resolve to
# the same weights and embedded character set, so one RapidOCR pass with the family's code loses nothing.
# A language with different weights needs its own family and is rejected when combined with others.
_RAPIDOCR_FAMILY = {"de": "ppocrv6-multi", "en": "ppocrv6-multi"}
_RAPIDOCR_FAMILY_LANG = {"ppocrv6-multi": "iso:de"}

_DOCLING_LOGGERS = ("docling", "docling_core", "docling_ibm_models", "docling_parse")


class UnsupportedMediaType(ValueError):
    """Input is not a PDF or a supported raster image."""


class UnsupportedDocumentOptions(ValueError):
    """The requested option combination cannot run on this host or with this engine."""


class DocumentConversionFailed(RuntimeError):
    """Docling could not convert the input. The message carries no document content."""


def _installed_tesseract_languages(binary: str) -> frozenset[str]:
    """Return the language packs reported by ``tesseract --list-langs`` (stdout or stderr)."""
    try:
        completed = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnsupportedDocumentOptions(f"could not query tesseract language packs: {exc}") from exc
    packs: set[str] = set()
    for line in (completed.stdout + completed.stderr).splitlines():
        token = line.strip()
        if token and not token.startswith("List of available languages"):
            packs.add(token)
    return frozenset(packs)


def _host_platform() -> str:
    return sys.platform


def _validate_options(options: DocumentOptions, accelerator: str) -> None:
    if accelerator not in ("cpu", "cuda", "metal"):
        raise UnsupportedDocumentOptions(f"unknown accelerator {accelerator!r}")
    if accelerator == "metal" and _host_platform() != "darwin":
        raise UnsupportedDocumentOptions("accelerator 'metal' requires macOS")
    if accelerator == "cuda" and _host_platform() == "darwin":
        raise UnsupportedDocumentOptions("accelerator 'cuda' is not available on macOS")
    if options.ocr == "off":
        return
    if options.ocr_engine == "ocrmac" and _host_platform() != "darwin":
        raise UnsupportedDocumentOptions("ocr_engine 'ocrmac' requires macOS (Apple Vision)")
    if options.ocr_engine == "tesseract":
        binary = shutil.which("tesseract")
        if binary is None:
            raise UnsupportedDocumentOptions("ocr_engine 'tesseract' requires the tesseract binary on PATH")
        installed = _installed_tesseract_languages(binary)
        for lang in options.languages:
            pack = _TESSERACT_LANG[lang]
            if pack not in installed:
                raise UnsupportedDocumentOptions(
                    f"tesseract language pack {pack!r} (for language {lang!r}) is not installed; "
                    f"installed packs: {', '.join(sorted(installed)) or 'none'}"
                )
    if options.ocr_engine == "rapidocr":
        _rapidocr_lang(options.languages)


def _rapidocr_lang(languages: list[str]) -> str:
    """Return the one RapidOCR language code that serves ``languages``.

    Languages in one recognizer family share weights, so a single pass with the family's code is lossless.
    Mixed families are rejected rather than silently truncated.
    """
    families = {_RAPIDOCR_FAMILY[lang] for lang in languages}
    if len(families) != 1:
        raise UnsupportedDocumentOptions(
            f"ocr_engine 'rapidocr' needs languages that share one recognizer; got {', '.join(languages)}"
        )
    return _RAPIDOCR_FAMILY_LANG[families.pop()]


def _ocr_options(options: DocumentOptions):  # type: ignore[no-untyped-def]
    mode = OcrMode.FULL_PAGE if options.ocr == "force" else OcrMode.DEFAULT
    if options.ocr_engine == "ocrmac":
        return OcrMacOptions(lang=[_OCRMAC_LANG[lang] for lang in options.languages], mode=mode)
    if options.ocr_engine == "rapidocr":
        return RapidOcrOptions(lang=[_rapidocr_lang(options.languages)], mode=mode)
    return TesseractCliOcrOptions(lang=[_TESSERACT_LANG[lang] for lang in options.languages], mode=mode)


def _pipeline_options(options: DocumentOptions, accelerator: str) -> PdfPipelineOptions:
    device = {
        "cpu": AcceleratorDevice.CPU,
        "cuda": AcceleratorDevice.CUDA,
        "metal": AcceleratorDevice.MPS,
    }[accelerator]
    pipeline = PdfPipelineOptions(
        do_ocr=options.ocr != "off",
        accelerator_options=AcceleratorOptions(device=device),
    )
    if options.ocr != "off":
        pipeline.ocr_options = _ocr_options(options)
    return pipeline


@lru_cache(maxsize=4)
def _converter(ocr: str, ocr_engine: str, languages: tuple[str, ...], accelerator: str) -> DocumentConverter:
    # Converters load layout/table/OCR models; cache them per pipeline shape.
    options = DocumentOptions(ocr=ocr, ocr_engine=ocr_engine, languages=list(languages))
    pipeline = _pipeline_options(options, accelerator)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline),
        }
    )


def _check_media(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in PDF_SUFFIXES | IMAGE_SUFFIXES:
        raise UnsupportedMediaType(f"unsupported document type {suffix or '(none)'}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _WarningCollector(logging.Handler):
    """Collects Docling WARNING records emitted during one conversion (e.g. table-cell matching)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] not in _DOCLING_LOGGERS:
            return
        lines = record.getMessage().strip().splitlines()
        message = lines[0][:300] if lines else ""
        if message and message not in self.messages:
            self.messages.append(message)


def _block_text(item: object, document: DoclingDocument) -> str:
    if isinstance(item, TableItem):
        return item.export_to_markdown(doc=document)
    if isinstance(item, FormulaItem):
        return (item.text or "").strip() or FORMULA_PLACEHOLDER
    if isinstance(item, TextItem):
        return item.text
    return ""


def _pages(document: DoclingDocument) -> list[Page]:
    by_page: dict[int, list[PageBlock]] = {}
    for item, _level in document.iterate_items(with_groups=False, traverse_pictures=False):
        if isinstance(item, PictureItem) or not item.prov:
            continue
        prov = item.prov[0]
        text = _block_text(item, document)
        if not text.strip():
            continue
        bbox = None
        if prov.bbox is not None:
            bbox = BBox(
                l=prov.bbox.l,
                t=prov.bbox.t,
                r=prov.bbox.r,
                b=prov.bbox.b,
                coord_origin=str(prov.bbox.coord_origin.value),
            )
        by_page.setdefault(prov.page_no, []).append(PageBlock(text=text, label=str(item.label.value), bbox=bbox))
    return [
        Page(number=n, text="\n\n".join(b.text for b in blocks), blocks=blocks)
        for n, blocks in sorted(by_page.items())
    ]


def _seconds(result: object, keys: tuple[str, ...]) -> float:
    timings = getattr(result, "timings", {}) or {}
    total = 0.0
    for key in keys:
        item = timings.get(key)
        if item is not None:
            total += float(sum(item.times))
    return round(total, 3)


def _ocr_engine_identity(engine: str) -> tuple[str, str | None, str]:
    """Return (model name, model revision, accelerator actually used by that OCR engine)."""
    if engine == "ocrmac":
        # Apple Vision runs on Apple Silicon's GPU/Neural Engine; recorded as the closest Metal-class path.
        return "apple-vision", None, "metal"
    if engine == "rapidocr":
        # onnxruntime CPU provider only; the layout stage may still run on CUDA.
        return "rapidocr-ppocr", importlib.metadata.version("rapidocr"), "cpu"
    return "tesseract-cli", None, "cpu"


def _layout_spec():  # type: ignore[no-untyped-def]
    return PdfPipelineOptions().layout_options.model_spec


def convert_document(path: Path, *, options: DocumentOptions, accelerator: Accelerator = "cpu") -> JobResult:
    """Convert one PDF or raster image to a :class:`JobResult`.

    ``options.page_start``/``page_end`` restrict Docling's own page range, so only those
    pages are processed. ``accelerator`` is the device requested for layout/table models;
    OCR engines report their own accelerator in provenance.

    Raises:
        UnsupportedMediaType: input is not PDF/PNG/JPEG/TIFF.
        UnsupportedDocumentOptions: option/engine/host combination cannot run.
        DocumentConversionFailed: Docling rejected or failed the input.
    """
    path = Path(path)
    _check_media(path)
    _validate_options(options, accelerator)

    converter = _converter(options.ocr, options.ocr_engine, tuple(options.languages), accelerator)
    page_range = (options.page_start or 1, options.page_end or sys.maxsize)

    collector = _WarningCollector()
    loggers = [logging.getLogger(name) for name in _DOCLING_LOGGERS]
    for logger in loggers:
        logger.addHandler(collector)
    try:
        result = converter.convert(path, page_range=page_range, raises_on_error=False)
    finally:
        for logger in loggers:
            logger.removeHandler(collector)

    if result.status == ConversionStatus.FAILURE:
        raise DocumentConversionFailed("docling could not convert the document")

    document = result.document
    warnings: list[str] = list(collector.messages)
    warnings.extend(f"docling: {err.error_message[:300]}" for err in result.errors)

    markdown = document.export_to_markdown(image_placeholder=IMAGE_PLACEHOLDER)
    raw_text = document.export_to_text()
    if options.ocr == "off" and path.suffix.lower() in IMAGE_SUFFIXES:
        warnings.append("image input with ocr='off' yields no text")

    formula_count = markdown.count(FORMULA_PLACEHOLDER)
    if formula_count:
        warnings.append(f"{formula_count} formula(s) not decoded; placeholders kept")

    spec = _layout_spec()
    stages = [
        StageProvenance(
            stage="layout",
            engine="docling-layout+tableformer",
            model=spec.repo_id,
            model_revision=spec.revision,
            accelerator=accelerator,
            seconds=_seconds(result, ("layout", "layout_postprocess", "table_structure")),
        )
    ]
    if options.ocr != "off":
        model, revision, ocr_accelerator = _ocr_engine_identity(options.ocr_engine)
        stages.append(
            StageProvenance(
                stage="ocr",
                engine=options.ocr_engine,
                model=model,
                model_revision=revision,
                accelerator=ocr_accelerator,
                seconds=_seconds(result, ("ocr",)),
            )
        )

    return JobResult(
        kind="document",
        raw_text=raw_text,
        markdown=markdown,
        language=options.languages[0] if options.ocr != "off" else None,
        pages=_pages(document),
        warnings=warnings,
        provenance=Provenance(
            stages=stages,
            worker_id=None,
            runtime_version=f"docling=={importlib.metadata.version('docling')}",
            settings_revision=None,
            input_sha256=_sha256(path),
        ),
    )


__all__ = [
    "DocumentConversionFailed",
    "UnsupportedDocumentOptions",
    "UnsupportedMediaType",
    "convert_document",
]
