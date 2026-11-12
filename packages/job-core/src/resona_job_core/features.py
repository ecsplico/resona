"""Job features: the release-dependent parts of an execution snapshot a worker must know.

A worker on an older release validates options with ``extra="forbid"`` (or silently
ignores what it does not know), so the hub hands a job only to a worker that knows every
feature the job uses. Features are named ``<kind>.<option>`` for each option key in the
snapshot's ``options`` and :data:`INSTRUCTIONS` for a postprocess profile carrying user
instructions.

Workers report :func:`known_features` in their heartbeat. Workers predating that report
are judged by their release through :data:`LEGACY_RELEASE_FEATURES`, which is frozen at the
last release before reporting: a new option needs no entry there. A new value of an
existing option that older releases reject needs a new feature name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from .options import AudioOptions, DocumentOptions, TextOptions

INSTRUCTIONS = "instructions"
"""The job's postprocess profile carries user instructions for its LLM steps."""

_OPTION_MODELS: dict[str, type[BaseModel]] = {
    "audio": AudioOptions,
    "document": DocumentOptions,
    "text": TextOptions,
}


def _options(kind: str, *names: str) -> frozenset[str]:
    return frozenset(f"{kind}.{name}" for name in names)


_V0_1_0 = _options("audio", "translate", "word_timestamps", "vad_filter") | _options(
    "document", "languages", "ocr", "ocr_engine", "page_start", "page_end", "pipeline", "image_scale",
    "ocr_min_confidence",
)
_V0_2_0 = _V0_1_0 | {INSTRUCTIONS}
_V0_2_1 = _V0_2_0 | _options("document", "remove_repeated_headers")
_V0_2_3 = _V0_2_1 | _options("audio", "diarize")

LEGACY_RELEASE_FEATURES: tuple[tuple[tuple[int, int, int], frozenset[str]], ...] = (
    ((0, 1, 0), _V0_1_0),
    ((0, 2, 0), _V0_2_0),
    ((0, 2, 1), _V0_2_1),
    ((0, 2, 3), _V0_2_3),
)
"""Features of releases whose workers report none, ascending; frozen (see module docstring)."""

_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def known_features() -> frozenset[str]:
    """Every feature the installed release knows: the option fields of each job kind plus instructions."""
    known = {f"{kind}.{name}" for kind, model in _OPTION_MODELS.items() for name in model.model_fields}
    return frozenset(known | {INSTRUCTIONS})


def required_features(kind: str, options: Mapping[str, Any] | None, *, instructions: bool = False) -> frozenset[str]:
    """The features a job with these snapshot ``options`` (keys as stored) uses."""
    required = {f"{kind}.{name}" for name in (options or {})}
    if instructions:
        required.add(INSTRUCTIONS)
    return frozenset(required)


def release_features(release: str | None) -> frozenset[str]:
    """Features of a worker release that reports none: the newest legacy entry at or below it.

    An unparseable or older release knows only the first release's options.
    """
    match = _RELEASE.match(release or "")
    version = tuple(int(part) for part in match.groups()) if match else None
    features = LEGACY_RELEASE_FEATURES[0][1]
    for since, entry in LEGACY_RELEASE_FEATURES:
        if version is not None and version >= since:
            features = entry
    return features


def worker_features(reported: Iterable[str] | None, release: str | None) -> frozenset[str]:
    """What a worker knows: its reported features, else those recorded for its release."""
    return frozenset(reported) if reported is not None else release_features(release)


__all__ = [
    "INSTRUCTIONS",
    "LEGACY_RELEASE_FEATURES",
    "known_features",
    "release_features",
    "required_features",
    "worker_features",
]
