"""Engine recommendations from measured accuracy and speed.

A port of T3 Code's ``recommendSpeechModels`` (``packages/shared/src/speech.ts``) with its
policy file (``recommendation_policy.json``, copied verbatim). Each model carries a
word error rate in percent per language and the seconds it takes to transcribe 30 s of
audio. For the wanted languages:

- a model lacking a measurement for any wanted language, or its speed, gets no role;
- its error is the geometric mean over the wanted languages, ``worst`` the highest one;
- models whose worst language reaches ``maxLanguageErrorPercent`` are not *useful*; when
  no model is useful, the least poor one is still ``recommended`` and no other role is given;
- ``recommended`` has the lowest cost (error + ``waitWeight`` × wait above
  ``freeWaitSeconds``) among the models within ``overallMaxWaitSeconds`` (all models if none is);
- ``fast`` is the most accurate model quicker than the recommended one and within
  ``fastMaxWaitSeconds``, else the quickest quicker one;
- ``accurate`` is the most accurate model with a lower error than the recommended one and
  within ``accurateMaxWaitSeconds``.

A model keeps only its first role (recommended, fast, accurate). Ties go to the earlier
model. T3 calls the second role ``faster``; Resona's contract names it ``fast``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

from .results import JobResult

Role = Literal["recommended", "fast", "accurate"]
ROLES: tuple[Role, ...] = ("recommended", "fast", "accurate")


def load_policy() -> dict[str, float]:
    """The packaged policy (T3's ``speechRecommendationPolicy.json``)."""
    text = resources.files(__package__).joinpath("recommendation_policy.json").read_text(encoding="utf-8")
    return json.loads(text)


POLICY: dict[str, float] = load_policy()


@dataclass(frozen=True)
class MeasuredModel:
    """One candidate's measurements: WER in percent per language, seconds per 30 s of audio."""

    id: str
    error_percent: Mapping[str, float]
    seconds_per_30s: float | None


@dataclass(frozen=True)
class Recommendation:
    role: Role
    error_percent: float  # geometric mean over the wanted languages
    seconds_per_30s: float


@dataclass(frozen=True)
class _Scored:
    id: str
    error: float
    worst: float
    wait: float
    cost: float


def _measured(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value >= 0


def seconds_per_30s(result: JobResult, audio_seconds: float | None) -> float | None:
    """Seconds the ``asr`` stage of ``result`` took per 30 s of audio (model loading excluded).

    None without an ``asr`` stage or a positive audio length.
    """
    asr = [stage.seconds for stage in result.provenance.stages if stage.stage == "asr"]
    if not asr or not audio_seconds or audio_seconds <= 0:
        return None
    return sum(asr) / audio_seconds * 30


def _geometric_mean(values: list[float]) -> float:
    # exp(mean(log)) as in T3; a zero error makes the mean zero (JS: exp(-Infinity) = 0).
    if any(value == 0 for value in values):
        return 0.0
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _lowest(items: Iterable[_Scored], score) -> _Scored | None:
    return min(items, key=score, default=None)


def recommend(
    models: Iterable[MeasuredModel],
    languages: Iterable[str],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Recommendation]:
    """Roles for the models that earn one, keyed by model id in role order."""
    policy = POLICY if policy is None else policy
    wanted = list(dict.fromkeys(languages))
    roles: dict[str, Recommendation] = {}
    if not wanted:
        return roles
    scored: list[_Scored] = []
    for model in models:
        errors = [model.error_percent.get(language) for language in wanted]
        wait = model.seconds_per_30s
        if not _measured(wait) or not all(_measured(error) for error in errors):
            continue
        error = _geometric_mean(errors)
        scored.append(_Scored(
            id=model.id,
            error=error,
            worst=max(errors),
            wait=wait,
            cost=error + policy["waitWeight"] * max(0.0, wait - policy["freeWaitSeconds"]),
        ))
    if not scored:
        return roles
    useful = [m for m in scored if m.worst < policy["maxLanguageErrorPercent"]]
    pool = useful or scored
    within_budget = [m for m in pool if m.wait <= policy["overallMaxWaitSeconds"]]
    if useful:
        best = _lowest(within_budget or pool, lambda m: m.cost)
    else:
        best = _lowest(pool, lambda m: m.worst + m.error / 1000)
    fast = accurate = None
    if useful:
        quicker = [m for m in pool if m.wait < best.wait]
        fast = _lowest([m for m in quicker if m.wait <= policy["fastMaxWaitSeconds"]], lambda m: m.error)
        if fast is None:
            fast = _lowest(quicker, lambda m: m.wait + m.error / 1000)
        accurate = _lowest(
            [m for m in pool if m.error < best.error and m.wait <= policy["accurateMaxWaitSeconds"]],
            lambda m: m.error,
        )
    for model, role in ((best, "recommended"), (fast, "fast"), (accurate, "accurate")):
        if model is not None and model.id not in roles:
            roles[model.id] = Recommendation(role=role, error_percent=model.error, seconds_per_30s=model.wait)
    return roles


__all__ = [
    "POLICY", "ROLES", "MeasuredModel", "Recommendation", "Role", "load_policy", "recommend", "seconds_per_30s",
]
