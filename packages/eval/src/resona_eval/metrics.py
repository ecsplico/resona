"""Text normalization and error metrics shared by evaluations and ``benchmarks/``.

Normalizations are versioned: every report records the identifiers below, so a
change to the rules needs a new identifier and new baselines.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import Protocol

import jiwer

TEXT_NORMALIZATION = "text-v1"
"""lowercase, ’ → ', every non-word character → space, whitespace collapsed (umlauts kept)."""

MARKDOWN_NORMALIZATION = "markdown-v1"
"""HTML comments (Docling placeholders) removed, then ``text-v1``; table pipes and
heading marks vanish as punctuation."""

NORMALIZATIONS = {
    TEXT_NORMALIZATION: "lowercase; ’→'; non-word characters → space; whitespace collapsed",
    MARKDOWN_NORMALIZATION: "HTML comments removed; then text-v1",
}

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_markdown(markdown: str) -> str:
    return normalize(_HTML_COMMENT.sub(" ", markdown or ""))


def _error_rate(measure, ref: str, hyp: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return float(measure(ref, hyp))


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate of already-normalized strings."""
    return _error_rate(jiwer.wer, reference, hypothesis)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate of already-normalized strings."""
    return _error_rate(jiwer.cer, reference, hypothesis)


def _normalizer(markdown: bool):
    return normalize_markdown if markdown else normalize


def score(reference: str, hypothesis: str, *, markdown: bool = False) -> dict:
    """WER/CER after ``text-v1`` (``markdown-v1`` with ``markdown=True``) normalization,
    plus normalized word counts."""
    normalizer = _normalizer(markdown)
    ref = normalizer(reference)
    hyp = normalizer(hypothesis)
    return {
        "wer": wer(ref, hyp),
        "cer": cer(ref, hyp),
        "ref_words": len(ref.split()),
        "hyp_words": len(hyp.split()),
    }


def word_alignment(reference: str, hypothesis: str, *, markdown: bool = False) -> list[dict]:
    """Word-level edit alignment of the normalized texts, one entry per aligned word.

    Each entry is ``{"op", "ref", "hyp"}`` with ``op`` one of ``equal``, ``substitute``,
    ``insert`` (hypothesis word absent from the reference: ``ref`` is None) or ``delete``
    (reference word the hypothesis lacks: ``hyp`` is None). The words are the ``text-v1``
    (``markdown-v1`` with ``markdown=True``) normalized words, and the alignment is the
    minimum-edit one WER counts, so ``substitute + insert + delete`` entries equal the
    word errors.
    """
    normalizer = _normalizer(markdown)
    ref = normalizer(reference).split()
    hyp = normalizer(hypothesis).split()
    if not ref or not hyp:
        if ref:
            return [{"op": "delete", "ref": word, "hyp": None} for word in ref]
        return [{"op": "insert", "ref": None, "hyp": word} for word in hyp]
    out = jiwer.process_words(" ".join(ref), " ".join(hyp))
    entries: list[dict] = []
    for chunk in out.alignments[0]:
        ref_words = ref[chunk.ref_start_idx:chunk.ref_end_idx]
        hyp_words = hyp[chunk.hyp_start_idx:chunk.hyp_end_idx]
        if chunk.type == "equal":
            entries.extend({"op": "equal", "ref": r, "hyp": h} for r, h in zip(ref_words, hyp_words))
        elif chunk.type == "substitute":
            entries.extend({"op": "substitute", "ref": r, "hyp": h} for r, h in zip(ref_words, hyp_words))
        elif chunk.type == "delete":
            entries.extend({"op": "delete", "ref": r, "hyp": None} for r in ref_words)
        else:
            entries.extend({"op": "insert", "ref": None, "hyp": h} for h in hyp_words)
    return entries


def inserted_words(hypothesis: str) -> int:
    """Words emitted where the reference is silence (``text-v1``)."""
    return len(normalize(hypothesis).split())


def markdown_table_cells(markdown: str) -> set[str]:
    """Normalized cell texts of every Markdown pipe table in ``markdown``."""
    cells: set[str] = set()
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        for cell in stripped.strip("|").split("|"):
            value = normalize(cell)
            if value:
                cells.add(value)
    return cells


def missing_table_cells(markdown: str, required: list[str]) -> list[str]:
    """Required cells that do not appear as a cell of a Markdown table."""
    present = markdown_table_cells(markdown)
    return [cell for cell in required if normalize(cell) not in present]


def reading_order_failures(markdown: str, anchors: list[str]) -> int:
    """Anchors that are missing or appear before the previous anchor (``markdown-v1``)."""
    text = normalize_markdown(markdown)
    position = 0
    failures = 0
    for anchor in anchors:
        needle = normalize(anchor)
        index = text.find(needle, position)
        if index < 0:
            failures += 1
            continue
        position = index + len(needle)
    return failures


class Turn(Protocol):
    """A reference speaker turn (``resona_eval.corpus.SpeakerTurn``)."""

    speaker: str
    start: float
    end: float
    text: str


class SpeakerSegment(Protocol):
    """A hypothesis segment (``resona_job_core.Segment``); ``speaker`` None = unattributed."""

    start: float
    end: float
    text: str
    speaker: int | None


def _best_matching(weights: Mapping[tuple[Hashable, Hashable], float]) -> float:
    """Largest total weight of a one-to-one mapping between hypothesis and reference labels.

    ``weights`` maps ``(hyp, ref)`` to the score that pair earns when mapped. Exact: a DP
    over subsets of the smaller label side (fine for the ≤ 8 speakers a diarizer emits).
    """
    hyps = sorted({h for h, _ in weights}, key=repr)
    refs = sorted({r for _, r in weights}, key=repr)
    if len(refs) > len(hyps):
        hyps, refs = refs, hyps
        weights = {(r, h): w for (h, r), w in weights.items()}
    best = {0: 0.0}  # bitmask of used ``refs`` → best total
    for h in hyps:
        step = dict(best)
        for mask, total in best.items():
            for j, r in enumerate(refs):
                w = weights.get((h, r), 0.0)
                if mask & (1 << j) or w <= 0:
                    continue
                key = mask | (1 << j)
                if total + w > step.get(key, -1.0):
                    step[key] = total + w
        best = step
    return max(best.values())


def speaker_attribution(reference_turns: Sequence[Turn], hypothesis_segments: Sequence[SpeakerSegment]) -> float:
    """Share of reference words whose aligned hypothesis word carries the right speaker.

    Words are ``text-v1`` normalized and aligned by :func:`word_alignment`; only
    ``equal``/``substitute`` pairs can count. Hypothesis speakers are mapped one-to-one onto
    reference speakers so that the most words count; an unmapped or missing speaker is
    wrong. Reference without words: 1.0.
    """
    ref = [(word, t.speaker) for t in reference_turns for word in normalize(t.text).split()]
    if not ref:
        return 1.0
    hyp = [(word, s.speaker) for s in hypothesis_segments for word in normalize(s.text).split()]
    alignment = word_alignment(" ".join(w for w, _ in ref), " ".join(w for w, _ in hyp))
    pairs: dict[tuple[Hashable, Hashable], float] = {}
    i = j = 0
    for entry in alignment:
        if entry["op"] in ("equal", "substitute"):
            hyp_speaker = hyp[j][1]
            if hyp_speaker is not None:
                key = (hyp_speaker, ref[i][1])
                pairs[key] = pairs.get(key, 0.0) + 1
            i, j = i + 1, j + 1
        elif entry["op"] == "delete":
            i += 1
        else:
            j += 1
    return _best_matching(pairs) / len(ref)


def _active(spans: Iterable[tuple[float, float, Hashable]], start: float, end: float) -> set[Hashable]:
    return {label for s, e, label in spans if s <= start and e >= end}


def der(reference_turns: Sequence[Turn], hypothesis_segments: Sequence[SpeakerSegment]) -> float:
    """Time-based diarization error rate: (miss + false alarm + confusion) / reference speech.

    Collar 0, overlapping speech scored (a span where two reference speakers talk counts
    twice), hypothesis speakers mapped one-to-one onto reference speakers to minimise the
    error. A hypothesis segment without speaker is speech that maps to nobody. No
    reference speech: 0.0 without hypothesis speech, else 1.0.
    """
    ref = [(t.start, t.end, t.speaker) for t in reference_turns if t.end > t.start]
    # Unattributed segments stay apart (one label each) so they never merge into a speaker.
    hyp = [(s.start, s.end, ("speaker", s.speaker) if s.speaker is not None else ("unattributed", k))
           for k, s in enumerate(hypothesis_segments) if s.end > s.start]
    bounds = sorted({t for s, e, _ in ref + hyp for t in (s, e)})
    total = scored = 0.0
    overlap: dict[tuple[Hashable, Hashable], float] = {}
    for start, end in zip(bounds, bounds[1:]):
        span = end - start
        refs, hyps = _active(ref, start, end), _active(hyp, start, end)
        total += span * len(refs)
        scored += span * max(len(refs), len(hyps))
        for h in hyps:
            if h[0] == "unattributed":
                continue
            for r in refs:
                overlap[(h, r)] = overlap.get((h, r), 0.0) + span
    if total == 0:
        return 0.0 if scored == 0 else 1.0
    return (scored - _best_matching(overlap)) / total


def speaker_count_error(reference_turns: Sequence[Turn], hypothesis_segments: Sequence[SpeakerSegment]) -> int:
    """|hypothesis speakers − reference speakers|; segments without speaker name nobody."""
    hyp = {s.speaker for s in hypothesis_segments if s.speaker is not None}
    return abs(len(hyp) - len({t.speaker for t in reference_turns}))
