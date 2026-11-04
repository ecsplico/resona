"""The committed evaluation corpus: ``eval/corpus/MANIFEST.json`` plus its fixture files.

The corpus hash is the SHA-256 of MANIFEST.json; the manifest pins every file's
SHA-256, so the hash covers the references and the fixture bytes together.

A document fixture may carry job ``options`` (e.g. ``{"ocr": "force"}``), typed as
:class:`DocumentOptions` so a bad manifest fails at load; they are merged over the
default document options (the fixture language) of every evaluation job of that fixture.
Audio fixtures carry no options.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from resona_job_core.options import DocumentOptions, Language

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
    options: DocumentOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def _audio_has_no_options(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == "audio" and "options" in data:
            raise ValueError("options are only supported for document fixtures")
        return data

    @model_validator(mode="after")
    def _document_language_is_supported(self) -> Fixture:
        if (self.kind == "document" and (self.options is None or self.options.languages is None)
                and self.language not in get_args(Language)):
            raise ValueError(f"language {self.language!r} is not a document language")
        return self

    def document_options(self) -> DocumentOptions:
        """Default document job options (the fixture language) with the fixture's options merged over them."""
        explicit = self.options.model_dump(exclude_unset=True) if self.options is not None else {}
        return DocumentOptions.model_validate({"languages": [self.language], **explicit})


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
        raise CorpusError(f"invalid {manifest_path}: {_describe(exc, raw)}") from None
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
        if hashlib.sha256(path.read_bytes()).hexdigest() != fixture.sha256:
            raise CorpusError(f"fixture {fixture.id!r}: {fixture.file} does not match its manifest SHA-256")
    return Corpus(root=root, sha256=hashlib.sha256(raw).hexdigest(), fixtures=tuple(manifest.fixtures))


def _describe(exc: ValidationError, raw: bytes) -> str:
    """Validation errors named by fixture id and field."""
    try:
        ids = [f.get("id") if isinstance(f, dict) else None for f in json.loads(raw)["fixtures"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        ids = []
    problems = []
    for err in exc.errors():
        loc = err["loc"]
        if len(loc) < 2 or loc[0] != "fixtures" or not isinstance(loc[1], int) or loc[1] >= len(ids):
            problems.append(f"{'.'.join(map(str, loc)) or 'manifest'}: {err['msg']}")
            continue
        field = ".".join(map(str, loc[2:]))
        if loc[2:3] == ("options",):
            problems.append(f"fixture {ids[loc[1]]!r}: invalid document options ({field})")
        else:
            problems.append(f"fixture {ids[loc[1]]!r}: {field + ': ' if field else ''}{err['msg']}")
    return "; ".join(dict.fromkeys(problems))
