"""Write a job result as ``<stem>.md`` plus ``<stem>.result.json``.

The pair is written all-or-nothing: both files are rendered and staged as
temporary files in the target directory, then renamed into place one after the
other. If the second rename fails, the first is rolled back (a file it replaced
under ``force`` is restored), so a failure leaves neither new file and a retry
starts clean. Without ``force`` an existing target is never replaced: the rename
is a hard link that fails if the name exists.
"""
from __future__ import annotations

import os
import secrets
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


def output_paths(source: Path, output_dir: Optional[Path], suffix: Optional[str] = None) -> OutputPaths:
    """``<stem>.md`` + ``<stem>.result.json``; with ``suffix`` the stem is ``<stem>.<suffix>``."""
    directory = output_dir or source.parent
    stem = f"{source.stem}.{suffix}" if suffix else source.stem
    return OutputPaths(
        markdown=directory / f"{stem}.md",
        result=directory / f"{stem}.result.json",
    )


def _stage(path: Path, data: str) -> Path:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def _promote(staged: Path, path: Path, *, force: bool) -> None:
    if force:
        os.replace(staged, path)
        return
    try:
        os.link(staged, path)
    except FileExistsError:
        raise OutputExists(path) from None


def _backup(path: Path) -> Optional[Path]:
    """Hard-link an existing ``path`` to a fresh hidden name so a replaced file can be restored."""
    while True:
        backup = path.with_name(f".{path.name}.{secrets.token_hex(6)}.bak")
        try:
            os.link(path, backup)
        except FileNotFoundError:
            return None
        except FileExistsError:
            continue
        return backup


def result_metadata(source: str, result: JobResult, *, job_id: Optional[int],
                    private: bool, execution_profile: Optional[str],
                    postprocess_profile: Optional[str]) -> dict[str, Any]:
    """Frontmatter for the Markdown file (``source``: the input's name), taken from the result's provenance."""
    primary = next((s for s in result.provenance.stages if s.stage in ("asr", "layout", "ocr")), None)
    return {
        "source": source,
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
    """Write ``<stem>.result.json`` (the validated JobResult) and ``<stem>.md`` as one unit.

    Raises OutputExists without touching either file when one exists and not ``force``.
    Any failure leaves both targets as they were before the call.
    """
    if not force:
        existing = paths.existing()
        if existing:
            raise OutputExists(existing[0])
    markdown = render_markdown(result.markdown or result.raw_text, meta)
    payload = result.model_dump_json(indent=2) + "\n"
    paths.markdown.parent.mkdir(parents=True, exist_ok=True)

    pair = ((paths.result, payload), (paths.markdown, markdown))
    staged: list[Path] = []
    backups: list[Optional[Path]] = []
    promoted: list[tuple[Path, Optional[Path]]] = []
    try:
        for target, data in pair:
            staged.append(_stage(target, data))
        for (target, _), tmp in zip(pair, staged):
            backup = _backup(target) if force else None
            backups.append(backup)
            _promote(tmp, target, force=force)
            promoted.append((target, backup))
    except BaseException:
        for target, backup in reversed(promoted):
            if backup is not None:
                os.replace(backup, target)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        for leftover in [*staged, *(b for b in backups if b is not None)]:
            leftover.unlink(missing_ok=True)
