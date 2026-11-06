"""Text normalization and error metrics shared by evaluations and ``benchmarks/``.

Normalizations are versioned: every report records the identifiers below, so a
change to the rules needs a new identifier and new baselines.
"""

from __future__ import annotations

import re

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
