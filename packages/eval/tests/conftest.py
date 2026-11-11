from pathlib import Path

import pytest
from resona_eval import FixtureRun, load_corpus
from resona_eval.corpus import Fixture
from resona_job_core import JobResult, Provenance, Segment, StageProvenance

CORPUS_DIR = Path(__file__).resolve().parents[3] / "eval" / "corpus"


@pytest.fixture(scope="session")
def corpus():
    return load_corpus(CORPUS_DIR)


def _table_rows(cells: list[str], lines: list[str]) -> list[list[str]]:
    """Rows of the table whose cells, space-joined row by row, are ``lines`` 1..n of the reference."""
    for width in range(1, len(cells) + 1):
        rows = [cells[i:i + width] for i in range(0, len(cells), width)]
        if len(cells) % width == 0 and [" ".join(r) for r in rows] == lines[1:1 + len(rows)]:
            return rows
    raise AssertionError("table cells do not match the reference lines")


def perfect_markdown(fixture: Fixture) -> str:
    """Markdown a flawless document conversion of ``fixture`` would produce."""
    lines = fixture.reference.splitlines()
    if not fixture.table_cells:
        return "\n\n".join(lines)
    rows = _table_rows(fixture.table_cells, lines)
    table = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    table += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    rest = lines[1 + len(rows):]
    parts = [f"## {lines[0]}", "\n".join(table)]
    if rest:
        parts += [f"## {rest[0]}", *rest[1:]]
    return "\n\n".join(parts)


def perfect_segments(fixture: Fixture) -> list[Segment]:
    """Speaker sections a flawless diarized transcription of ``fixture`` would produce."""
    numbers: dict[str, int] = {}
    return [Segment(start=t.start, end=t.end, text=t.text, speaker=numbers.setdefault(t.speaker, len(numbers) + 1))
            for t in fixture.speakers]


def make_result(fixture: Fixture, *, text: str | None = None, markdown: str | None = None,
                raw_text: str | None = None, accelerator: str = "cpu",
                segments: list[Segment] | None = None) -> JobResult:
    if fixture.kind == "audio":
        stages = [StageProvenance(stage="asr", engine="faster-whisper", model="Systran/faster-whisper-large-v3",
                                  model_revision="rev-asr", accelerator=accelerator, seconds=1.0)]
        raw = fixture.reference if text is None else text
        return JobResult(kind="audio", raw_text=raw, markdown=raw, language="de",
                         segments=perfect_segments(fixture) if segments is None else segments,
                         provenance=Provenance(stages=stages, runtime_version="0.1.0", worker_id="w1"))
    stages = [StageProvenance(stage="layout", engine="docling", model="docling-standard",
                              model_revision="rev-layout", accelerator=accelerator, seconds=1.0)]
    md = perfect_markdown(fixture) if markdown is None else markdown
    return JobResult(kind="document", raw_text=md if raw_text is None else raw_text, markdown=md,
                     provenance=Provenance(stages=stages, runtime_version="0.1.0", worker_id="w1"))


@pytest.fixture
def perfect_runs(corpus):
    return [FixtureRun(fixture=f, result=make_result(f), job_id=i + 1) for i, f in enumerate(corpus.fixtures)]


@pytest.fixture
def result_for():
    return make_result


@pytest.fixture
def fixture_by_id(corpus):
    return {f.id: f for f in corpus.fixtures}
