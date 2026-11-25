"""T3 Code's ``recommendSpeechModels`` cases (speech.test.ts), reproduced on the Python port.

T3 measures each model on two tiers (accelerated, cpu); a Resona candidate has exactly one
accelerator, so each T3 case is expressed by passing the tier's seconds as the model's speed.
"""

import json
from importlib import resources

import pytest
from resona_job_core import JobResult, Provenance, StageProvenance
from resona_job_core.recommend import POLICY, MeasuredModel, recommend, seconds_per_30s


def model(id: str, errors: dict[str, float], wait: float | None) -> MeasuredModel:
    return MeasuredModel(id=id, error_percent=errors, seconds_per_30s=wait)


def roles(models, languages):
    return [(id, r.role) for id, r in recommend(models, languages).items()]


# (id, errors, accelerated wait, cpu wait), as in T3's fixture.
T3_MODELS = [
    ("balanced", {"en": 4, "de": 16}, 2, 7),
    ("fast", {"en": 7, "de": 7}, 1, 3),
    ("accurate", {"en": 2, "de": 2}, 8, 20),
    ("incomplete", {"en": 1}, 1, 1),
]


def tier(models, index):
    return [model(id, errors, waits[index]) for id, errors, *waits in models]


def test_policy_is_t3s_file_and_packaged():
    text = resources.files("resona_job_core").joinpath("recommendation_policy.json").read_text()
    assert json.loads(text) == POLICY
    assert POLICY["freeWaitSeconds"] == 3 and POLICY["waitWeight"] == 0.5
    assert POLICY["maxLanguageErrorPercent"] == 20 and POLICY["overallMaxWaitSeconds"] == 5


def test_balances_all_requested_languages_and_assigns_each_model_its_first_role():
    accelerated, cpu = tier(T3_MODELS, 0), tier(T3_MODELS, 1)
    assert roles(accelerated[:3], ["en"]) == [("balanced", "recommended"), ("fast", "fast"), ("accurate", "accurate")]
    assert roles(accelerated, ["en", "de", "de"]) == [("fast", "recommended"), ("accurate", "accurate")]
    assert roles(cpu[:3], ["en"]) == [("fast", "recommended"), ("balanced", "accurate")]
    assert roles(cpu, ["en", "de"]) == [("fast", "recommended")]


def test_offers_alternatives_only_when_they_improve_on_the_balanced_pick():
    assert roles([model("cpu-fast-gpu-slow", {"en": 3}, 4), model("balanced", {"en": 3.2}, 1)], ["en"]) == [
        ("balanced", "recommended"), ("cpu-fast-gpu-slow", "accurate"),
    ]
    assert roles([model("equal-accuracy", {"en": 4}, 8), model("balanced", {"en": 4}, 2)], ["en"]) == [
        ("balanced", "recommended"),
    ]
    assert roles([model("balanced", {"en": 4}, 12), model("outside-budget", {"en": 2}, 30)], ["en"]) == [
        ("balanced", "recommended"),
    ]


def test_does_not_invent_measurements_or_cover_missing_languages():
    accelerated = tier(T3_MODELS, 0)
    assert recommend(accelerated, []) == {}
    assert recommend(accelerated, ["ja"]) == {}
    assert recommend(
        [model("missing-speed", {"en": 1, "de": 1}, None), model("missing-accuracy", {"en": 1}, 1)],
        ["en", "de"],
    ) == {}
    assert roles([model("no-cpu", {"en": 1}, 1)], ["en"]) == [("no-cpu", "recommended")]
    assert recommend([model("no-cpu", {"en": 1}, None)], ["en"]) == {}
    assert roles([model("cpu-only", {"en": 1}, 2)], ["en"]) == [("cpu-only", "recommended")]


def test_prefers_useful_accuracy_before_speed_and_relaxes_the_wait_budget_before_quality():
    assert roles([model("poor", {"en": 25}, 1), model("slow", {"en": 4}, 12)], ["en"]) == [("slow", "recommended")]
    assert roles(
        [model("worse", {"en": 30, "de": 40}, 1), model("least-poor", {"en": 29, "de": 30}, 20)], ["en", "de"]
    ) == [("least-poor", "recommended")]
    assert roles([model("zero", {"en": 0, "de": 0}, 1)], ["en", "de"]) == [("zero", "recommended")]


def test_reports_geometric_mean_error_and_speed():
    result = recommend([model("a", {"en": 4, "de": 16}, 2.5)], ["en", "de"])["a"]
    assert result.role == "recommended"
    assert result.error_percent == pytest.approx(8.0)
    assert result.seconds_per_30s == 2.5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_rejects_unusable_numbers(bad):
    assert recommend([model("a", {"en": bad}, 1)], ["en"]) == {}
    assert recommend([model("a", {"en": 1}, bad)], ["en"]) == {}


def _result(stages):
    return JobResult(kind="audio", raw_text="", markdown="", provenance=Provenance(stages=stages))


def test_seconds_per_30s_uses_the_asr_stage_only():
    stages = [
        StageProvenance(stage="asr", engine="e", model="m", accelerator="cpu", seconds=3.0),
        StageProvenance(stage="diarize", engine="d", model="m", accelerator="cpu", seconds=9.0),
    ]
    assert seconds_per_30s(_result(stages), 60.0) == pytest.approx(1.5)
    assert seconds_per_30s(_result(stages), None) is None
    assert seconds_per_30s(_result(stages), 0.0) is None
    assert seconds_per_30s(_result(stages[1:]), 60.0) is None
