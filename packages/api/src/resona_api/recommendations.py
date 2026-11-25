"""Engine recommendations for ``GET /v1/engines`` from the organization's own measurements.

Two sources, never foreign benchmark numbers:

- ``baseline``: the current promoted baseline of each (execution profile, platform). It is
  attributed to the profile's single audio candidate on that platform (none or several →
  skipped). Per fixture with a ``wer`` and a recorded ``language``: WER in percent per
  language (mean over fixtures) and ``seconds_per_30s`` (mean over the fixtures that carry
  it). When several baselines map to one candidate, the most recently promoted wins.
- ``testbench``: the latest completed ``bench`` run per (candidate, corrected audio item),
  as on the leaderboard (draft runs favour the reference and are left out). WER per item
  language; speed from the run's captured result: ``asr`` stage seconds per 30 s of audio,
  where the audio length is the end of the last transcript segment (the hub does not decode
  the recording; trailing silence is therefore not counted).

A candidate uses the baseline when it covers every wanted language and has a speed, else
the testbench under the same rule, else it stays unmeasured (no recommendation). The
roles come from :func:`resona_job_core.recommend.recommend` (T3 Code's policy).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from resona_job_core import PLATFORM_OF_ACCELERATOR, Candidate, OrganizationSettings
from resona_job_core.recommend import MeasuredModel, recommend, seconds_per_30s
from sqlmodel import Session, select

from . import testbench
from .db.models import Baseline, Evaluation, TestbenchItem, TestbenchRun

BASELINE = "baseline"
TESTBENCH = "testbench"


@dataclass(frozen=True)
class Measurement:
    error_percent: dict[str, float]  # WER in percent per language
    seconds_per_30s: float | None

    def covers(self, languages: list[str]) -> bool:
        return self.seconds_per_30s is not None and all(lang in self.error_percent for lang in languages)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _measurement(errors: dict[str, list[float]], speeds: list[float]) -> Measurement:
    return Measurement(
        error_percent={lang: _mean(values) for lang, values in errors.items()},
        seconds_per_30s=_mean(speeds) if speeds else None,
    )


def baseline_measurements(
    session: Session, organization_id: str, settings: OrganizationSettings
) -> dict[Candidate, Measurement]:
    current: dict[tuple[str, str], Baseline] = {}
    for baseline in session.exec(
        select(Baseline).where(Baseline.organization_id == organization_id).order_by(Baseline.revision)
    ).all():
        current[(baseline.execution_profile, baseline.platform)] = baseline
    measured: dict[Candidate, tuple[Baseline, Measurement]] = {}
    for (profile_name, platform), baseline in current.items():
        profile = settings.execution_profiles.get(profile_name)
        if profile is None:
            continue
        matching = [c for c in profile.candidates("audio") if PLATFORM_OF_ACCELERATOR.get(c.accelerator) == platform]
        if len(matching) != 1:
            continue
        evaluation = session.get(Evaluation, baseline.evaluation_id)
        if evaluation is None:
            continue
        errors: dict[str, list[float]] = {}
        speeds: list[float] = []
        for metrics in json.loads(evaluation.fixtures_json).values():
            language, wer = metrics.get("language"), metrics.get("wer")
            if not isinstance(language, str) or not isinstance(wer, (int, float)):
                continue
            errors.setdefault(language, []).append(wer * 100)
            speed = metrics.get("seconds_per_30s")
            if isinstance(speed, (int, float)):
                speeds.append(float(speed))
        if not errors:
            continue
        [candidate] = matching
        earlier = measured.get(candidate)
        if earlier is None or baseline.created_at > earlier[0].created_at:
            measured[candidate] = (baseline, _measurement(errors, speeds))
    return {candidate: measurement for candidate, (_, measurement) in measured.items()}


def testbench_measurements(session: Session, organization_id: str) -> dict[Candidate, Measurement]:
    items = {
        item.id: item
        for item in session.exec(
            select(TestbenchItem).where(
                TestbenchItem.organization_id == organization_id,
                TestbenchItem.kind == "audio",
                TestbenchItem.reference_text.is_not(None),
            )
        ).all()
    }
    if not items:
        return {}
    latest: dict[tuple[str, int], TestbenchRun] = {}
    for run in session.exec(
        select(TestbenchRun)
        .where(
            TestbenchRun.organization_id == organization_id,
            TestbenchRun.role == testbench.BENCH,
            TestbenchRun.status == "completed",
            TestbenchRun.item_id.in_(list(items)),
        )
        .order_by(TestbenchRun.id)
    ).all():
        if run.wer is not None:
            latest[(run.candidate, run.item_id)] = run
    storage_id = testbench.storage_id_of(session, organization_id)
    errors: dict[str, dict[str, list[float]]] = {}
    speeds: dict[str, list[float]] = {}
    for (candidate, item_id), run in latest.items():
        errors.setdefault(candidate, {}).setdefault(items[item_id].language, []).append(run.wer * 100)
        result = testbench.load_run_result(storage_id, run)
        if result is None or not result.segments:
            continue
        speed = seconds_per_30s(result, max(segment.end for segment in result.segments))
        if speed is not None:
            speeds.setdefault(candidate, []).append(speed)
    return {
        Candidate.model_validate_json(candidate): _measurement(by_language, speeds.get(candidate, []))
        for candidate, by_language in errors.items()
    }


def recommendations(
    session: Session,
    organization_id: str,
    settings: OrganizationSettings,
    candidates: Iterable[Candidate],
    languages: Iterable[str],
) -> dict[Candidate, dict]:
    """``{candidate: {role, error_percent, seconds_per_30s, basis}}`` for candidates that earn a role."""
    wanted = list(dict.fromkeys(languages))
    candidates = list(dict.fromkeys(candidates))
    if not wanted or not candidates:
        return {}
    sources: list[tuple[str, Callable[[], dict[Candidate, Measurement]]]] = [
        (BASELINE, lambda: baseline_measurements(session, organization_id, settings)),
        (TESTBENCH, lambda: testbench_measurements(session, organization_id)),
    ]
    loaded: dict[str, dict[Candidate, Measurement]] = {}
    chosen: dict[str, tuple[Candidate, str]] = {}
    models: list[MeasuredModel] = []
    for candidate in candidates:
        for basis, load in sources:
            if basis not in loaded:
                loaded[basis] = load()
            measurement = loaded[basis].get(candidate)
            if measurement is not None and measurement.covers(wanted):
                key = candidate.model_dump_json()
                chosen[key] = (candidate, basis)
                models.append(MeasuredModel(
                    id=key, error_percent=measurement.error_percent, seconds_per_30s=measurement.seconds_per_30s,
                ))
                break
    return {
        chosen[key][0]: {
            "role": rec.role,
            "error_percent": round(rec.error_percent, 2),
            "seconds_per_30s": round(rec.seconds_per_30s, 2),
            "basis": chosen[key][1],
        }
        for key, rec in recommend(models, wanted).items()
    }


__all__ = ["BASELINE", "TESTBENCH", "Measurement", "baseline_measurements", "recommendations",
           "testbench_measurements"]
