"""Deterministic title suggestion from a transcript.

Mirrors `app/utils/titleSuggestion.ts`; both are checked against the cases in
`shared/titleSuggestion.cases.json`.
"""
from __future__ import annotations

import re

FILLERS = frozenset(
    "äh ähm ääh öhm ehm hm hmm mhm halt eben mal sozusagen quasi okay ok".split()
)
STOPWORDS = frozenset(
    "also ja nein so gut dann und oder aber jetzt nun ich wir du sie es man der die das "
    "den dem des ein eine einen einem einer eines zu zum zur von vom mit für auf in im an "
    "am bei beim ist sind war hat habe haben noch schon bitte danke".split()
)

MAX_TOKENS = 6
MAX_CHARS = 60

_PREFIX = re.compile(r"^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"[*_`]")
_SEGMENT_SPLIT = re.compile(r"[.!?;:\n]+")
_TOKEN = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")


def _strip_stopwords(tokens: list[str]) -> list[str]:
    start, end = 0, len(tokens)
    while start < end and tokens[start].lower() in STOPWORDS:
        start += 1
    while end > start and tokens[end - 1].lower() in STOPWORDS:
        end -= 1
    return tokens[start:end]


def _clean(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        line = _PREFIX.sub("", line)
        line = _LINK.sub(r"\1", line)
        lines.append(_EMPHASIS.sub("", line))
    return "\n".join(lines)


def suggest_title(text: str) -> str | None:
    for segment in _SEGMENT_SPLIT.split(_clean(text)):
        tokens = [t for t in _TOKEN.findall(segment) if t.lower() not in FILLERS]
        tokens = _strip_stopwords(tokens)
        if not any(len(t) >= 3 for t in tokens):
            continue
        tokens = _strip_stopwords(tokens[:MAX_TOKENS])
        title = " ".join(tokens)
        while len(title) > MAX_CHARS and len(tokens) > 1:
            tokens = _strip_stopwords(tokens[:-1])
            title = " ".join(tokens)
        title = title[:MAX_CHARS]
        if not title:
            continue
        return title[0].upper() + title[1:]
    return None
