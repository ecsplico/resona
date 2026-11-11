"""Score one job result against its corpus fixture.

Audio fixtures are scored on ``raw_text`` (the engine transcript before any
postprocessing); documents on ``markdown`` because table structure and reading
order only exist there. Postprocessing (polish) rewrites ``markdown`` and leaves
``raw_text`` as the engine produced it, so a plain run scores the engine's Markdown
and a ``--postprocess-profile`` run scores the polished Markdown, both after
``markdown-v1`` normalization. Diarization fixtures are scored on ``segments[].speaker``
against their reference turns; a diarization result with a segment lacking ``speaker``
(or no segments at all) attributes nothing: ``speaker_attribution`` 0 and ``der`` 1.0.
Only numbers leave this module, never text.
"""

from __future__ import annotations

from resona_job_core import JobResult

from .corpus import Fixture
from .metrics import (
    cer,
    der,
    inserted_words,
    missing_table_cells,
    normalize,
    normalize_markdown,
    reading_order_failures,
    speaker_attribution,
    speaker_count_error,
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
    if fixture.speakers:
        segments = result.segments
        attributed = bool(segments) and all(s.speaker is not None for s in segments)
        if "speaker_attribution" in fixture.checks:
            value = speaker_attribution(fixture.speakers, segments) if attributed else 0.0
            metrics["speaker_attribution"] = round(value, 4)
        if "der" in fixture.checks:
            metrics["der"] = round(der(fixture.speakers, segments), 4) if attributed else 1.0
        if "speaker_count" in fixture.checks:
            metrics["speaker_count_error"] = speaker_count_error(fixture.speakers, segments)
    return metrics
