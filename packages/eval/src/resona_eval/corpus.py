"""The committed evaluation corpus: ``eval/corpus/MANIFEST.json`` plus its fixture files.

The corpus hash is the SHA-256 of MANIFEST.json; the manifest pins every file's
SHA-256, so the hash covers the references and the fixture bytes together.

A fixture may carry job ``options``, typed by kind so a bad manifest fails at load:
document fixtures :class:`DocumentOptions` (e.g. ``{"ocr": "force"}``), merged over the
default document options (the fixture language); audio fixtures :class:`AudioOptions`
(e.g. ``{"diarize": true}``), merged over the defaults a normal job gets.

A diarization fixture (``options.diarize``) carries its reference ``speakers``: the
exact speaker turns ``{speaker, start, end, text}`` of the generated audio, in order of
start, whose texts together are the ``reference``. The speaker checks
(``speaker_attribution``, ``der``, ``speaker_count``) need such a fixture.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from resona_job_core.options import AudioOptions, DocumentOptions, Language

from .metrics import normalize

Check = Literal["wer", "cer", "silence", "table_cells", "reading_order", "speaker_attribution", "der",
                "speaker_count"]
SPEAKER_CHECKS = frozenset({"speaker_attribution", "der", "speaker_count"})


class CorpusError(Exception):
    pass


class SpeakerTurn(BaseModel):
    """One reference speaker turn: who spoke ``text`` from ``start`` to ``end`` seconds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ends_after_start(self) -> SpeakerTurn:
        if self.end <= self.start:
            raise ValueError("a speaker turn must end after it starts")
        return self


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
    options: DocumentOptions | AudioOptions | None = None
    speakers: list[SpeakerTurn] = []

    @field_validator("options", mode="before")
    @classmethod
    def _options_typed_by_kind(cls, value: Any, info: ValidationInfo) -> Any:
        if isinstance(value, dict):
            typed = AudioOptions if info.data.get("kind") == "audio" else DocumentOptions
            return typed.model_validate(value)
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Fixture:
        expected = AudioOptions if self.kind == "audio" else DocumentOptions
        if self.options is not None and not isinstance(self.options, expected):
            raise ValueError(f"{self.kind} fixtures take {expected.__name__}")
        if (self.kind == "document" and (self.options is None or self.options.languages is None)
                and self.language not in get_args(Language)):
            raise ValueError(f"language {self.language!r} is not a document language")
        diarize = isinstance(self.options, AudioOptions) and self.options.diarize
        if diarize != bool(self.speakers):
            raise ValueError("reference speakers belong exactly to fixtures with options.diarize")
        if SPEAKER_CHECKS & set(self.checks) and not diarize:
            raise ValueError("speaker checks need options.diarize")
        if self.speakers:
            if [t.start for t in self.speakers] != sorted(t.start for t in self.speakers):
                raise ValueError("speakers must be ordered by start")
            if normalize(" ".join(t.text for t in self.speakers)) != normalize(self.reference):
                raise ValueError("speaker turn texts must make up the reference")
        return self

    def document_options(self) -> DocumentOptions:
        """Default document job options (the fixture language) with the fixture's options merged over them."""
        explicit = self.options.model_dump(exclude_unset=True) if self.options is not None else {}
        return DocumentOptions.model_validate({"languages": [self.language], **explicit})

    def audio_options(self) -> AudioOptions:
        """The options a normal audio job gets by default with the fixture's options merged over them."""
        explicit = self.options.model_dump(exclude_unset=True) if self.options is not None else {}
        return AudioOptions.model_validate(explicit)


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
        entries = [f if isinstance(f, dict) else {} for f in json.loads(raw)["fixtures"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        entries = []
    ids = [f.get("id") for f in entries]
    problems = []
    for err in exc.errors():
        loc = err["loc"]
        if len(loc) < 2 or loc[0] != "fixtures" or not isinstance(loc[1], int) or loc[1] >= len(ids):
            problems.append(f"{'.'.join(map(str, loc)) or 'manifest'}: {err['msg']}")
            continue
        field = ".".join(map(str, loc[2:]))
        if loc[2:3] == ("options",):
            kind = "audio" if entries[loc[1]].get("kind") == "audio" else "document"
            problems.append(f"fixture {ids[loc[1]]!r}: invalid {kind} options ({field})")
        else:
            problems.append(f"fixture {ids[loc[1]]!r}: {field + ': ' if field else ''}{err['msg']}")
    return "; ".join(dict.fromkeys(problems))
