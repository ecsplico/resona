"""Speaker turns and their assignment to transcript words. Pure Python, no ML imports.

The diarizer says who spoke when (:class:`Turn`); the ASR engine says which word was
said when (:class:`Word`). Each word gets the speaker whose turns cover most of it.
Assigning whole ASR segments instead loses speaker changes inside a segment (Whisper
segments routinely run across them), so segments are only the fallback for engines
that return no word timestamps.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """One stretch of speech of one speaker. ``speaker`` is 1-based."""

    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class Word:
    """One timed transcript token. ``text`` keeps the engine's spacing (Whisper words
    carry their leading space), so concatenating words reproduces the transcript."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Section:
    """Consecutive words of one speaker."""

    speaker: int
    words: tuple[Word, ...]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return "".join(word.text for word in self.words).strip()


def renumber(turns: Iterable[Turn]) -> list[Turn]:
    """Sort turns by start and number speakers 1, 2, ... in order of first appearance."""
    ordered = sorted(turns, key=lambda t: (t.start, t.speaker))
    numbers: dict[int, int] = {}
    for turn in ordered:
        numbers.setdefault(turn.speaker, len(numbers) + 1)
    return [Turn(t.start, t.end, numbers[t.speaker]) for t in ordered]


def speaker_at(start: float, end: float, turns: Sequence[Turn], starts: Sequence[float] | None = None) -> int | None:
    """The speaker whose turns overlap ``[start, end]`` the most; without overlap the speaker
    of the nearest turn (ASR word times and diarizer turns disagree by tens of milliseconds,
    and words in pauses still belong to someone). ``None`` only when there are no turns.

    ``turns`` must be sorted by start; ``starts`` (their start times) may be passed to
    avoid recomputing them per call.
    """
    if not turns:
        return None
    if starts is None:
        starts = [t.start for t in turns]
    cover: dict[int, float] = {}
    # Turns are short relative to the recording; look only at those that can overlap.
    hi = bisect.bisect_right(starts, end)
    for turn in turns[:hi]:
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > 0:
            cover[turn.speaker] = cover.get(turn.speaker, 0.0) + overlap
    if cover:
        return max(cover, key=lambda speaker: (cover[speaker], -speaker))
    middle = (start + end) / 2
    nearest = min(turns, key=lambda t: min(abs(middle - t.start), abs(middle - t.end)))
    return nearest.speaker


def assign_words(words: Sequence[Word], turns: Sequence[Turn]) -> list[int | None]:
    """Speaker of every word (see :func:`speaker_at`); ``turns`` sorted by start."""
    starts = [t.start for t in turns]
    return [speaker_at(w.start, w.end, turns, starts) for w in words]


def sections(words: Sequence[Word], speakers: Sequence[int | None]) -> list[Section]:
    """Group consecutive words of the same speaker. Words without a speaker (no turns at
    all) join the current section; leading ones join the first speaker's."""
    out: list[Section] = []
    pending: list[Word] = []
    for word, speaker in zip(words, speakers, strict=True):
        if speaker is None or (out and out[-1].speaker == speaker):
            if out:
                out[-1] = Section(out[-1].speaker, (*out[-1].words, word))
            else:
                pending.append(word)
            continue
        out.append(Section(speaker, (*pending, word)))
        pending = []
    return out


__all__ = ["Section", "Turn", "Word", "assign_words", "renumber", "sections", "speaker_at"]
