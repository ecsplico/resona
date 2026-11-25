"""Bundled filler-word blocks: `filler-words-de` and `filler-words-en`."""

import pytest

from resona_postprocess.compose import bundled_block
from resona_postprocess.replacements import apply_replacements


def _rules(name: str) -> list[dict]:
    block = bundled_block(name)
    assert block is not None and block.block is True
    (step,) = block.steps
    return step["rules"]


DE = _rules("filler-words-de")
EN = _rules("filler-words-en")


@pytest.mark.parametrize(("text", "expected"), [
    ("Ich habe äh keine Zeit", "Ich habe keine Zeit"),
    ("Ich habe ähm keine Zeit", "Ich habe keine Zeit"),
    ("Er sagte öh nein und öhm doch", "Er sagte nein und doch"),
    ("Hm, gut", "gut"),
    ("Hmm, mhm, gut", "gut"),
    ("ÄHM, GUT", "GUT"),
    # One adjacent comma goes with the filler.
    ("Ja, äh, ich komme", "Ja, ich komme"),
    ("Ja, äh, ähm, ich komme", "Ja, ich komme"),
    ("Ich komme ähm, wenn es geht", "Ich komme, wenn es geht"),
    ("Äh, ich habe", "ich habe"),
    # At the end of a sentence or line the punctuation stays.
    ("keine Zeit, äh.", "keine Zeit."),
    ("Zeit äh äh.", "Zeit."),
    ("Wirklich ähm?", "Wirklich?"),
    ("komme. Äh. Morgen", "komme. Morgen"),
    ("Äh. Ich", "Ich"),
    # No double space is left behind.
    ("Er sagte äh  ja", "Er sagte ja"),
])
def test_german_fillers_are_removed(text: str, expected: str) -> None:
    assert apply_replacements(text, DE) == expected


@pytest.mark.parametrize("text", [
    "Die Ähmlichkeit ist ähnlich",
    "Das Ohmmeter misst im Öhr",
    "Ein Hm-Laut",
    "Die Hummel brummt um acht Uhr",
    "Thema: Dosis 5 mg",
])
def test_german_words_containing_fillers_stay(text: str) -> None:
    assert apply_replacements(text, DE) == text


def test_german_block_leaves_english_um_alone() -> None:
    assert apply_replacements("Wir treffen uns um acht", DE) == "Wir treffen uns um acht"


@pytest.mark.parametrize(("text", "expected"), [
    ("Well uh I mean", "Well I mean"),
    ("So, um, I think", "So, I think"),
    ("Well uh, I mean", "Well, I mean"),
    ("Erm, yes", "yes"),
    ("The term is, erm.", "The term is."),
    ("Um. Okay", "Okay"),
    ("Hmm", ""),
    ("Ummm, uhh, right", "right"),
])
def test_english_fillers_are_removed(text: str, expected: str) -> None:
    assert apply_replacements(text, EN) == expected


@pytest.mark.parametrize("text", ["an umbrella", "uh-huh", "the hummus is warm", "a germ in the term"])
def test_english_words_containing_fillers_stay(text: str) -> None:
    assert apply_replacements(text, EN) == text


@pytest.mark.parametrize(("rules", "text", "expected"), [
    (DE, "Erste Zeile äh\nzweite Zeile ähm, weiter", "Erste Zeile\nzweite Zeile, weiter"),
    (DE, "Absatz eins\n\näh\n\nAbsatz zwei", "Absatz eins\n\n\n\nAbsatz zwei"),
    (DE, "- Punkt eins\n- äh Punkt zwei", "- Punkt eins\n- Punkt zwei"),
    (EN, "line one um\nline two", "line one\nline two"),
])
def test_fillers_never_eat_newlines(rules: list[dict], text: str, expected: str) -> None:
    out = apply_replacements(text, rules)
    assert out == expected
    assert out.count("\n") == text.count("\n")
