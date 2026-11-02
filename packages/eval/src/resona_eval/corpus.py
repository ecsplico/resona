"""The committed evaluation corpus: ``eval/corpus/MANIFEST.json`` plus its fixture files.

The corpus hash is the SHA-256 of MANIFEST.json; the manifest pins every file's
SHA-256, so the hash covers the references and the fixture bytes together.

A document fixture may carry job ``options`` (e.g. ``{"ocr": "force"}``); they are
merged over the default document options of every evaluation job of that fixture
and validated as :class:`DocumentOptions` when the corpus is loaded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from resona_job_core.options import DocumentOptions

Check = Literal["wer", "cer", "silence", "table_cells", "reading_order"]


class CorpusError(Exception):
    pass


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Literal["audio", "document"]
    file: str
    language: str
    source: str
    license: str
    sha256: str
    reference: str
    checks: list[Check]
    table_cells: list[str] = []
    reading_order: list[str] = []
    options: dict[str, Any] = {}

    def document_options(self) -> DocumentOptions:
        """Default document job options (the fixture language) with the fixture's options merged over them."""
        return DocumentOptions.model_validate({"languages": [self.language], **self.options})


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    generator: str
    fixtures: list[Fixture]


@dataclass(frozen=True)
class Corpus:
    root: Path
    sha256: str
    fixtures: tuple[Fixture, ...]

    def path(self, fixture: Fixture) -> Path:
        return self.root / fixture.file


def load_corpus(root: Path) -> Corpus:
    """Load and verify the corpus at ``root`` (file hashes, unique ids, plain file names)."""
    manifest_path = root / "MANIFEST.json"
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise CorpusError(f"cannot read {manifest_path}: {exc.strerror}") from None
    try:
        manifest = Manifest.model_validate_json(raw)
    except ValidationError as exc:
        raise CorpusError(f"invalid {manifest_path}: {exc.error_count()} error(s)") from None
    seen: set[str] = set()
    for fixture in manifest.fixtures:
        if fixture.id in seen:
            raise CorpusError(f"duplicate fixture id {fixture.id!r}")
        seen.add(fixture.id)
        if Path(fixture.file).name != fixture.file:
            raise CorpusError(f"fixture {fixture.id!r}: file must be a plain name inside the corpus")
        path = root / fixture.file
        if path.is_symlink() or not path.is_file():
            raise CorpusError(f"fixture {fixture.id!r}: {fixture.file} is missing")
        _check_options(fixture)
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture.sha256:
            raise CorpusError(f"fixture {fixture.id!r}: {fixture.file} does not match its manifest SHA-256")
    return Corpus(root=root, sha256=hashlib.sha256(raw).hexdigest(), fixtures=tuple(manifest.fixtures))


def _check_options(fixture: Fixture) -> None:
    if fixture.kind != "document":
        if fixture.options:
            raise CorpusError(f"fixture {fixture.id!r}: options are only supported for document fixtures")
        return
    try:
        fixture.document_options()
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) or "options" for err in exc.errors()})
        raise CorpusError(f"fixture {fixture.id!r}: invalid document options ({', '.join(fields)})") from None
