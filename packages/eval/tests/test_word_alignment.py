"""Word-level alignment shares WER's normalization and minimum edit count."""
import pytest
from resona_eval.metrics import normalize, normalize_markdown, score, wer, word_alignment


def ops(entries):
    return [e["op"] for e in entries]


def test_identical_texts_are_all_equal_after_normalization():
    entries = word_alignment("Der Patient, klagt über Kopfschmerzen.", "der patient klagt über KOPFSCHMERZEN")

    assert ops(entries) == ["equal"] * 5
    assert [e["ref"] for e in entries] == ["der", "patient", "klagt", "über", "kopfschmerzen"]
    assert all(e["ref"] == e["hyp"] for e in entries)


def test_substitution_insertion_and_deletion_are_labelled_with_their_words():
    entries = word_alignment("a b c d", "a x c d e")

    assert {"op": "substitute", "ref": "b", "hyp": "x"} in entries
    assert {"op": "insert", "ref": None, "hyp": "e"} in entries
    assert ops(word_alignment("a b c", "a c")) == ["equal", "delete", "equal"]
    assert word_alignment("a b c", "a c")[1] == {"op": "delete", "ref": "b", "hyp": None}


@pytest.mark.parametrize("reference, hypothesis", [
    ("eins zwei drei vier fünf", "eins zwo drei fünf sechs"),
    ("a b c d e f g", "g f e d c b a"),
    ("hallo welt", ""),
    ("", "hallo welt"),
    ("gleich", "gleich"),
])
def test_alignment_error_count_matches_wer(reference, hypothesis):
    entries = word_alignment(reference, hypothesis)
    errors = sum(1 for e in entries if e["op"] != "equal")
    ref_words = len(normalize(reference).split())
    assert [e["ref"] for e in entries if e["ref"] is not None] == normalize(reference).split()
    assert [e["hyp"] for e in entries if e["hyp"] is not None] == normalize(hypothesis).split()
    if ref_words:
        assert errors / ref_words == pytest.approx(wer(normalize(reference), normalize(hypothesis)))


def test_empty_sides_yield_pure_deletions_or_insertions():
    assert word_alignment("a b", "") == [
        {"op": "delete", "ref": "a", "hyp": None},
        {"op": "delete", "ref": "b", "hyp": None},
    ]
    assert word_alignment("", "a") == [{"op": "insert", "ref": None, "hyp": "a"}]
    assert word_alignment("", "") == []


def test_markdown_mode_drops_comments_and_table_marks():
    reference = "# Titel\n\n<!-- image -->\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    hypothesis = "Titel a b 1 2"

    assert ops(word_alignment(reference, hypothesis, markdown=True)) == ["equal"] * 5
    assert normalize_markdown(reference) == "titel a b 1 2"
    # Without markdown mode the HTML comment words survive and show up as deletions.
    assert "delete" in ops(word_alignment(reference, hypothesis))


def test_score_markdown_flag_uses_markdown_normalization():
    reference = "<!-- image -->\nHallo Welt"

    assert score(reference, "hallo welt", markdown=True)["wer"] == 0.0
    assert score(reference, "hallo welt")["wer"] > 0.0
