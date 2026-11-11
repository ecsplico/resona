"""The release quality gate: a pure function of candidate and baseline metrics.

Absolute checks (always): no missing table cell, no reading-order failure, no word
emitted on a silence fixture. Regression checks (against a comparable baseline):
no per-fixture WER/CER/DER more than ``TOLERANCE`` absolute above the baseline, no
speaker attribution more than ``TOLERANCE`` below it, no candidate fixture or metric
absent from the baseline (``baseline_required:<id>``) and no baseline fixture absent
from the candidate (``missing_fixture:<id>``). ``speaker_count_error`` is reported and
must be in the baseline too, but is not compared.

Without any comparable baseline the outcome is ``baseline_required``: the gate does
not pass, but ``record()`` reports whether the absolute checks passed, which is what
the hub needs to allow the first baseline promotion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

TOLERANCE = 0.02
ABSOLUTE_METRICS = ("missing_cells", "order_failures", "silence_insertions")
HIGHER_IS_WORSE = ("wer", "cer", "der")
LOWER_IS_WORSE = ("speaker_attribution",)
REGRESSION_METRICS = HIGHER_IS_WORSE + LOWER_IS_WORSE
REPORTED_METRICS = ("speaker_count_error",)
BASELINE_REQUIRED = "baseline_required"


@dataclass(frozen=True)
class Gate:
    failures: tuple[str, ...]
    baseline_required: bool

    @property
    def passed(self) -> bool:
        return not self.failures and not self.baseline_required

    @property
    def outcome(self) -> str:
        if self.failures:
            return "gate_failed"
        return BASELINE_REQUIRED if self.baseline_required else "passed"

    def record(self) -> dict:
        """The ``gate`` object stored with the evaluation on the hub."""
        return {"passed": not self.failures, "failures": list(self.failures)}


def evaluate_gate(
    candidate: Mapping[str, Mapping[str, float]],
    baseline: Mapping[str, Mapping[str, float]] | None,
    *,
    tolerance: float = TOLERANCE,
) -> Gate:
    failures: list[str] = []
    for fixture_id in sorted(candidate):
        metrics = candidate[fixture_id]
        for name in ABSOLUTE_METRICS:
            if metrics.get(name, 0) > 0:
                failures.append(f"{name}:{fixture_id}")
        if baseline is None:
            continue
        reference = baseline.get(fixture_id)
        if reference is None:
            failures.append(f"{BASELINE_REQUIRED}:{fixture_id}")
            continue
        for name in REGRESSION_METRICS + REPORTED_METRICS:
            if name not in metrics:
                continue
            if name not in reference:
                failures.append(f"{BASELINE_REQUIRED}:{fixture_id}:{name}")
                continue
            if name in REPORTED_METRICS:
                continue
            worse = metrics[name] - reference[name] if name in HIGHER_IS_WORSE else reference[name] - metrics[name]
            if worse > tolerance + 1e-9:
                failures.append(f"regression:{fixture_id}:{name}")
    if baseline is not None:
        failures.extend(f"missing_fixture:{fixture_id}" for fixture_id in sorted(set(baseline) - set(candidate)))
    return Gate(failures=tuple(failures), baseline_required=baseline is None)
