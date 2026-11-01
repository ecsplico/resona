from pathlib import Path

import pytest
from resona_eval import FixtureRun, load_corpus
from resona_eval.corpus import Fixture
from resona_job_core import JobResult, Provenance, StageProvenance

CORPUS_DIR = Path(__file__).resolve().parents[3] / "eval" / "corpus"


@pytest.fixture(scope="session")
def corpus():
    return load_corpus(CORPUS_DIR)


def perfect_markdown(fixture: Fixture) -> str:
    """Markdown a flawless document conversion of ``fixture`` would produce."""
    if not fixture.table_cells:
        return "\n\n".join(fixture.reference.splitlines())
    lines = fixture.reference.splitlines()
    rows = [fixture.table_cells[i:i + 4] for i in range(0, len(fixture.table_cells), 4)]
    table = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * 4]
    table += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    rest = lines[1 + len(rows):]
    return "\n\n".join([f"## {lines[0]}", "\n".join(table), f"## {rest[0]}", *rest[1:]])


def make_result(fixture: Fixture, *, text: str | None = None, markdown: str | None = None,
                accelerator: str = "cpu") -> JobResult:
    if fixture.kind == "audio":
        stages = [StageProvenance(stage="asr", engine="faster-whisper", model="Systran/faster-whisper-large-v3",
                                  model_revision="rev-asr", accelerator=accelerator, seconds=1.0)]
        raw = fixture.reference if text is None else text
        return JobResult(kind="audio", raw_text=raw, markdown=raw, language="de",
                         provenance=Provenance(stages=stages, runtime_version="0.1.0", worker_id="w1"))
    stages = [StageProvenance(stage="layout", engine="docling", model="docling-standard",
                              model_revision="rev-layout", accelerator=accelerator, seconds=1.0)]
    md = perfect_markdown(fixture) if markdown is None else markdown
    return JobResult(kind="document", raw_text=md, markdown=md,
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
