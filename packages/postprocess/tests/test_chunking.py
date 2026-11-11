"""Markdown chunking for long-document LLM steps: block boundaries, never inside a table."""

import pytest

from resona_postprocess.chunking import markup_signature, split_markdown


def _paragraph(n: int, words: int = 30) -> str:
    return " ".join(f"wort{n}x{i}" for i in range(words))


def _table(rows: int, tag: str) -> str:
    lines = ["| Spalte A | Spalte B | Spalte C |", "|---|---|---|"]
    lines += [f"| {tag}{r}a | {tag}{r}b | {tag}{r}c |" for r in range(rows)]
    return "\n".join(lines)


def _document() -> str:
    blocks = ["# Gutachten", _paragraph(1), _table(12, "t"), _paragraph(2), "<!-- image -->",
              _paragraph(3), _table(30, "u"), "## Abschnitt", _paragraph(4), _paragraph(5)]
    return "\n\n".join(blocks) + "\n"


def _table_runs(text: str) -> list[str]:
    runs, current = [], []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("|"):
            current.append(line)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


@pytest.mark.parametrize("limit", [200, 350, 500, 800, 1500, 100_000])
def test_chunks_reassemble_exactly_and_never_cut_a_table(limit: int) -> None:
    text = _document()

    chunks = split_markdown(text, limit)

    assert "".join(chunks) == text
    for table in _table_runs(text):
        assert sum(table in chunk for chunk in chunks) == 1, "a table was split across chunks"


def test_a_table_longer_than_the_limit_stays_one_chunk() -> None:
    table = _table(40, "z")
    text = f"{_paragraph(1)}\n\n{table}\n\n{_paragraph(2)}\n"
    assert len(table) > 500

    chunks = split_markdown(text, 500)

    assert table in [c.strip() for c in chunks]


def test_chunks_end_at_blank_lines_when_blocks_fit() -> None:
    text = "\n\n".join(_paragraph(i, 20) for i in range(10)) + "\n"

    chunks = split_markdown(text, 800)

    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    assert all(c.endswith("\n\n") for c in chunks[:-1])


def test_oversized_paragraph_falls_back_to_line_boundaries() -> None:
    long_block = "\n".join(_paragraph(i, 15) for i in range(20))

    chunks = split_markdown(long_block, 600)

    assert "".join(chunks) == long_block
    assert all(len(c) <= 600 for c in chunks)
    assert all(c.endswith("\n") for c in chunks[:-1])


def test_fenced_block_is_never_split() -> None:
    fence = "```\n" + "\n\n".join(f"zeile {i} " * 10 for i in range(20)) + "\n```"
    text = f"{_paragraph(1)}\n\n{fence}\n\n{_paragraph(2)}\n"

    chunks = split_markdown(text, 300)

    assert sum(fence in chunk for chunk in chunks) == 1


def test_markup_signature_sees_placeholders_headings_and_table_shape() -> None:
    base = "# Titel\n\nText <!-- image -->\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"

    assert markup_signature(base) == markup_signature(base.replace("Text", "Texte"))
    assert markup_signature(base) != markup_signature(base.replace(" <!-- image -->", ""))
    assert markup_signature(base) != markup_signature(base.replace("# Titel", "## Titel"))
    assert markup_signature(base) != markup_signature(base.replace("| 1 | 2 |", "| 1 2 |"))


_DIARIZED = (
    "**Sprecher 1:** guten tag wie geht es ihnen\n\n"
    "**Sprecher 2:** danke gut\n\n"
    "weiter ohne marke im selben abschnitt\n\n"
    "**Sprecher 1:** schön\n"
)


def test_markup_signature_accepts_text_only_changes_in_a_diarized_transcript() -> None:
    polished = (_DIARIZED.replace("guten tag wie", "Guten Tag, wie")
                .replace("danke gut", "Danke, gut."))

    assert markup_signature(polished) == markup_signature(_DIARIZED)


@pytest.mark.parametrize("changed", [
    _DIARIZED.replace("**Sprecher 2:** danke gut", "danke gut"),  # label dropped
    _DIARIZED.replace("**Sprecher 2:** ", "**Sprecher 1:** "),  # speakers merged
    _DIARIZED.replace("**Sprecher 2:**", "**Anna:**"),  # renamed
    _DIARIZED.replace("ihnen\n\n**Sprecher 2:** ", "ihnen **Sprecher 2:** "),  # label moved into text
])
def test_markup_signature_rejects_changed_speaker_labels(changed: str) -> None:
    assert markup_signature(changed) != markup_signature(_DIARIZED)


def test_split_never_separates_a_speaker_label_from_its_text() -> None:
    text = "".join(f"**Sprecher {n % 2 + 1}:** {_paragraph(n)}\n\n" for n in range(10))

    chunks = split_markdown(text, 300)

    assert len(chunks) > 1
    assert all(chunk.startswith("**Sprecher ") for chunk in chunks)
