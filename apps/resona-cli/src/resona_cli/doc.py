"""``resona doc``: PDFs/images -> hub document jobs (or ``--local``) -> ``<stem>.md``."""
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from resona_client.client import DocumentOptions

from .hub import check_privacy_flags, fail
from .media import MediaRequest, run_media
from .transcribe import expand_inputs

EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tif", "tiff"}


def _page_range(pages: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """``a-b``, ``a-``, ``-b`` or ``a`` -> inclusive 1-based (start, end)."""
    if pages is None:
        return None, None
    start, sep, end = pages.partition("-")
    try:
        first = int(start) if start.strip() else None
        last = int(end) if end.strip() else None
    except ValueError:
        fail(f"invalid --pages {pages!r}: expected a-b, a-, -b or a")
    if not sep:
        last = first
    return first, last


def document_options(ocr: Optional[str], ocr_engine: Optional[str], languages: Optional[str],
                     pages: Optional[str]) -> DocumentOptions:
    page_start, page_end = _page_range(pages)
    try:
        return DocumentOptions(
            ocr=ocr,
            ocr_engine=ocr_engine,
            languages=[x.strip() for x in languages.split(",") if x.strip()] if languages else None,
            page_start=page_start,
            page_end=page_end,
        )
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        fail(f"invalid document options: {problems}")


def doc_files(
    inputs: list[str] = typer.Argument(
        ..., help="PDF/image files, glob patterns, or directories.", metavar="INPUTS..."),
    recursive: bool = typer.Option(False, "--recursive", "-r",
        help="Recurse into directories / use `**` in glob patterns."),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir",
        help="Directory for <stem>.md and <stem>.result.json (default: next to each input)."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outputs."),
    as_json: bool = typer.Option(False, "--json", help="Print a JSON report on stdout."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit and print job IDs; do not wait."),
    timeout: Optional[float] = typer.Option(None, "--timeout",
        help="Seconds to wait per job; the job keeps running on the hub afterwards."),
    private: bool = typer.Option(False, "--private",
        help="Require private processing (the default)."),
    allow_cloud: bool = typer.Option(False, "--allow-cloud",
        help="Allow cloud stages; still requires the organization to enable cloud processing."),
    execution_profile: Optional[str] = typer.Option(None, "--execution-profile",
        help="Execution profile (approved engine candidates), e.g. mac-private, cpu-private."),
    engine: Optional[str] = typer.Option(None, "--engine",
        help="Engine override; must be an approved candidate of the execution profile."),
    model: Optional[str] = typer.Option(None, "--model",
        help="Model override; must be an approved candidate of the execution profile."),
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Postprocess profile name (a JSON file or inline JSON only with --local)."),
    ocr: Optional[str] = typer.Option(None, "--ocr",
        help="auto (OCR bitmap regions only), force (full-page OCR) or off."),
    ocr_engine: Optional[str] = typer.Option(None, "--ocr-engine",
        help="ocrmac, rapidocr or tesseract; must be available in the execution profile."),
    languages: Optional[str] = typer.Option(None, "--languages",
        help="Comma-separated document languages: de, en."),
    pages: Optional[str] = typer.Option(None, "--pages",
        help="Inclusive 1-based page range: a-b, a-, -b or a."),
    local: bool = typer.Option(False, "--local",
        help="Process on this machine with cached hub settings instead of the hub."),
):
    """Convert PDFs and images to Markdown through the hub (or explicitly with --local).

    Writes <stem>.md and <stem>.result.json (raw text, pages, provenance). Nothing
    falls back automatically.
    """
    want_private = check_privacy_flags(private, allow_cloud)
    options = document_options(ocr, ocr_engine, languages, pages)
    files = expand_inputs(inputs, recursive, EXTENSIONS)
    if not files:
        typer.echo("No documents found.", err=True)
        raise typer.Exit(1)
    run_media(MediaRequest(
        kind="document",
        files=files,
        output_dir=output_dir,
        force=force,
        as_json=as_json,
        no_wait=no_wait,
        wait_timeout=timeout,
        private=want_private,
        execution_profile=execution_profile,
        language=None,
        engine=engine,
        model=model,
        profile=profile,
        options=options,
        local=local,
    ))
