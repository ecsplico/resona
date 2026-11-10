"""Deterministic recording title from a transcript (no model, no network).

Port of `deriveTitle` in `app/utils/titleSuggestion.ts`: the first Markdown
heading, else the first sentence's (or clause's) first meaningful words; None
when no usable word is left. Both are checked against
`shared/titleSuggestion.cases.json`; change the rule, this port and the
fixture together.
"""
from __future__ import annotations

import re

TITLE_MAX_CHARS = 60
TITLE_MAX_WORDS = 6
# A comma ends the title candidate only once this many words are collected.
MIN_CLAUSE_WORDS = 3

# Filler sounds and discourse particles that never belong in a title.
FILLERS = frozenset({
    "äh", "ähm", "öh", "öhm", "ehm", "hm", "hmm", "mhm", "äääh", "ääh", "ähh", "ähhm",
    "also", "ja", "so", "okay", "ok", "genau", "gut", "na", "nun", "naja",
})

# Spoken dictation commands (see resona-postprocess `default_replacements.json`).
COMMANDS = frozenset({
    "komma", "punkt", "absatz", "doppelpunkt", "fragezeichen", "ausrufezeichen",
    "semikolon", "strichpunkt", "bindestrich", "kapitel", "klammer", "anführungszeichen",
})

# Spoken commands that end a sentence.
SENTENCE_COMMANDS = frozenset({"punkt", "absatz", "fragezeichen", "ausrufezeichen", "doppelpunkt"})

# Function words a title should neither start nor end with.
STOPWORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "und", "oder", "aber", "mit", "über", "von", "vom", "zu", "zum", "zur", "für", "auf", "an",
    "am", "im", "in", "bei", "beim", "nach", "aus", "um", "wie", "dass", "ist", "sind", "war",
    "hat", "haben", "wird", "werden", "es", "ich", "wir", "sie", "er", "heute", "jetzt", "dann",
    "seit", "wegen", "bis", "ohne", "gegen", "unter", "vor", "durch", "sehr", "noch", "auch",
})

_TRAILING_PUNCT = re.compile(r"[\s.,;:!?…\-–—\"'„“”»«()\[\]]+\Z")
_EDGE_NON_WORD = re.compile(r"^[\W_]+|[\W_]+\Z")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", re.MULTILINE)
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"[#*_`>]+")
_ENDS_SENTENCE = re.compile(r"[.!?][\"'”»)]*\Z")
_LIST_NUMBER = re.compile(r"[0-9]+[.)]")
_TRAILING_CLAUSE = re.compile(r"[,;:]+\Z")


def _strip_trailing_punctuation(s: str) -> str:
    return _TRAILING_PUNCT.sub("", s)


def _cap_length(s: str) -> str:
    if len(s) <= TITLE_MAX_CHARS:
        return s
    cut = s[: TITLE_MAX_CHARS + 1]
    space = cut.rfind(" ")
    return _strip_trailing_punctuation(cut[:space] if space > 0 else s[:TITLE_MAX_CHARS])


def _finish(s: str) -> str:
    cleaned = _strip_trailing_punctuation(" ".join(s.split()))
    if not cleaned:
        return ""
    capped = _cap_length(cleaned)
    return capped[0].upper() + capped[1:]


def _bare(word: str) -> str:
    return _EDGE_NON_WORD.sub("", word.lower())


def _first_heading(text: str) -> str:
    m = _HEADING.search(text)
    return _finish(m.group(1)) if m else ""


def _first_sentence_words(text: str) -> list[str]:
    """Words of the first sentence (or clause, once three words are in) without fillers
    and dictation commands. A line break ends a sentence; list markers and Markdown
    links (text kept) are dropped per line."""
    words: list[str] = []
    for line in text.split("\n"):
        line = _MARKUP.sub(" ", _LINK.sub(r"\1", _LINE_PREFIX.sub("", line, count=1)))
        for token in line.split():
            key = _bare(token)
            ends_sentence = bool(_ENDS_SENTENCE.search(token)) and not _LIST_NUMBER.fullmatch(token)
            if key and key in SENTENCE_COMMANDS:
                if words:
                    return words
                continue
            if key == "komma" and len(words) >= MIN_CLAUSE_WORDS:
                return words
            if key and key not in FILLERS and key not in COMMANDS:
                words.append(_TRAILING_CLAUSE.sub("", token))
            if ends_sentence and words:
                return words
            if token.endswith((",", ";", ":")) and len(words) >= MIN_CLAUSE_WORDS:
                return words
        if words:
            return words
    return words


def derive_title(text: str) -> str | None:
    heading = _first_heading(text)
    if heading:
        return heading
    words = _first_sentence_words(text)
    while words and _bare(words[0]) in STOPWORDS:
        words = words[1:]
    words = words[:TITLE_MAX_WORDS]
    while words and _bare(words[-1]) in STOPWORDS:
        words = words[:-1]
    return _finish(" ".join(words)) or None
