"""Score one job result against its corpus fixture.

Audio fixtures are scored on ``raw_text`` (the engine transcript before any
postprocessing); documents on ``markdown`` because table structure and reading
order only exist there. Only numbers leave this module, never text.
"""

from __future__ import annotations

from resona_job_core import JobResult

from .corpus import Fixture
from .metrics import (
    cer,
    inserted_words,
    missing_table_cells,
    normalize,
    normalize_markdown,
    reading_order_failures,
    wer,
)

FixtureMetrics = dict[str, float | int]


class ScoringError(Exception):
    pass


def score_fixture(fixture: Fixture, result: JobResult) -> FixtureMetrics:
    if result.kind != fixture.kind:
        raise ScoringError(f"fixture {fixture.id!r} expects a {fixture.kind} result, got {result.kind}")
    metrics: FixtureMetrics = {}
    if fixture.kind == "audio":
        ref, hyp = normalize(fixture.reference), normalize(result.raw_text)
    else:
        ref, hyp = normalize(fixture.reference), normalize_markdown(result.markdown)
    if "wer" in fixture.checks:
        metrics["wer"] = round(wer(ref, hyp), 4)
    if "cer" in fixture.checks:
        metrics["cer"] = round(cer(ref, hyp), 4)
    if "silence" in fixture.checks:
        metrics["silence_insertions"] = inserted_words(result.raw_text)
    if "table_cells" in fixture.checks:
        metrics["missing_cells"] = len(missing_table_cells(result.markdown, fixture.table_cells))
    if "reading_order" in fixture.checks:
        metrics["order_failures"] = reading_order_failures(result.markdown, fixture.reading_order)
    return metrics
