"""Write a job result as ``<stem>.md`` plus ``<stem>.result.json``.

Both files are written to a temporary file in the target directory and then
renamed into place, so a reader never sees a partial file. Without ``force`` an
existing target is never replaced: the rename is a hard link that fails if the
name exists.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from resona_job_core import JobResult

from .markdown_output import render_markdown


@dataclass(frozen=True)
class OutputPaths:
    markdown: Path
    result: Path

    def existing(self) -> list[Path]:
        return [p for p in (self.markdown, self.result) if p.exists()]


class OutputExists(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} exists (use --force to overwrite)")
        self.path = path


def output_paths(source: Path, output_dir: Optional[Path]) -> OutputPaths:
    directory = output_dir or source.parent
    return OutputPaths(
        markdown=directory / f"{source.stem}.md",
        result=directory / f"{source.stem}.result.json",
    )


def _write_atomic(path: Path, data: str, *, force: bool) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        if force:
            os.replace(tmp, path)
        else:
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise OutputExists(path) from None
    finally:
        Path(tmp).unlink(missing_ok=True)


def result_metadata(source: Path, result: JobResult, *, job_id: Optional[int],
                    private: bool, execution_profile: Optional[str],
                    postprocess_profile: Optional[str]) -> dict[str, Any]:
    """Frontmatter for the Markdown file, taken from the result's provenance."""
    primary = next((s for s in result.provenance.stages if s.stage in ("asr", "layout", "ocr")), None)
    return {
        "source": source.name,
        "kind": result.kind,
        "job_id": job_id,
        "language": result.language,
        "engine": primary.engine if primary else None,
        "model": primary.model if primary else None,
        "accelerator": primary.accelerator if primary else None,
        "execution_profile": execution_profile,
        "profile": postprocess_profile,
        "private": private,
        "worker": result.provenance.worker_id,
        "settings_revision": result.provenance.settings_revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_result(paths: OutputPaths, result: JobResult, meta: dict[str, Any], *, force: bool) -> None:
    """Write ``<stem>.result.json`` (the validated JobResult) and ``<stem>.md``.

    Raises OutputExists without touching either file when one exists and not ``force``.
    """
    if not force:
        existing = paths.existing()
        if existing:
            raise OutputExists(existing[0])
    paths.markdown.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(paths.result, result.model_dump_json(indent=2) + "\n", force=force)
    _write_atomic(paths.markdown, render_markdown(result.markdown or result.raw_text, meta), force=force)
