"""Split Markdown into LLM-sized chunks on block boundaries, and guard chunk output.

``split_markdown`` never cuts inside a pipe table or a fenced code block, prefers
blank-line (block) boundaries and falls back to line boundaries only for a block
longer than the limit. The chunks concatenate to the input exactly.
"""

from __future__ import annotations

import re

_FORBIDDEN, _LINE, _BLOCK = 0, 1, 2
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = ("```", "~~~")


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _boundaries(lines: list[str]) -> list[int]:
    """Priority of cutting before line ``i`` (index 0 unused): forbidden, line or block."""
    prio = [_FORBIDDEN] * (len(lines) + 1)
    prio[len(lines)] = _BLOCK
    in_fence = False
    for i, line in enumerate(lines):
        if i > 0:
            prev = lines[i - 1]
            if in_fence:
                prio[i] = _FORBIDDEN
            elif _is_table_line(prev) and _is_table_line(line):
                prio[i] = _FORBIDDEN
            elif not prev.strip() and line.strip():
                prio[i] = _BLOCK
            else:
                prio[i] = _LINE
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
    return prio


def split_markdown(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters where possible.

    A chunk ends at the furthest block boundary within ``limit``; failing that, at the
    furthest line boundary; a single table or fenced block longer than ``limit`` stays
    whole (it is never cut). ``"".join(chunks) == text``.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    prio = _boundaries(lines)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    chunks: list[str] = []
    start = 0
    while start < len(lines):
        best_block = best_line = None
        first_beyond = None
        for end in range(start + 1, len(lines) + 1):
            if prio[end] == _FORBIDDEN:
                continue
            if offsets[end] - offsets[start] <= limit:
                if prio[end] == _BLOCK:
                    best_block = end
                best_line = end
            else:
                first_beyond = end
                break
        end = best_block or best_line or first_beyond or len(lines)
        chunks.append(text[offsets[start]:offsets[end]])
        start = end
    return chunks


def markup_signature(text: str) -> tuple:
    """Structure that a correction pass must leave untouched.

    HTML comments (Docling placeholders such as ``<!-- image -->`` and
    ``<!-- formula-not-decoded -->``) in order, the heading level of every heading line,
    and the number of cells of every table line.
    """
    comments = tuple(_COMMENT.findall(text))
    headings: list[int] = []
    table_rows: list[int] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if stripped[level:level + 1] in (" ", ""):
                headings.append(level)
        elif stripped.startswith("|"):
            table_rows.append(stripped.rstrip().count("|"))
    return comments, tuple(headings), tuple(table_rows)


def length_deviation(before: str, after: str) -> float:
    """Relative change in length; ``before`` must be non-empty."""
    return abs(len(after) - len(before)) / len(before)
