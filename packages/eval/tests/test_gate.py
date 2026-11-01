"""Gate demo: each documented failure mode fails the gate; missing baselines are explicit."""
from resona_eval import evaluate_gate, score_fixture
from resona_eval.evaluation import score_runs

BASELINE = {
    "de-speech": {"wer": 0.05, "cer": 0.02},
    "silence": {"silence_insertions": 0},
    "table-columns-pdf": {"cer": 0.01, "missing_cells": 0, "order_failures": 0},
}


def _candidate(**changes):
    candidate = {fid: dict(metrics) for fid, metrics in BASELINE.items()}
    for fid, update in changes.items():
        candidate[fid].update(update)
    return candidate


def test_unchanged_metrics_pass():
    gate = evaluate_gate(_candidate(), BASELINE)
    assert gate.passed and gate.outcome == "passed"
    assert gate.record() == {"passed": True, "failures": []}


def test_removed_table_cell_fails(fixture_by_id, result_for):
    table = fixture_by_id["table-columns-pdf"]
    damaged = result_for(table).markdown.replace("| Kälte C |", "|  |")
    metrics = score_fixture(table, result_for(table, markdown=damaged))
    gate = evaluate_gate(_candidate(**{"table-columns-pdf": metrics}), BASELINE)
    assert not gate.passed
    assert "missing_cells:table-columns-pdf" in gate.failures


def test_silence_insertion_fails(fixture_by_id, result_for):
    silence = fixture_by_id["silence"]
    metrics = score_fixture(silence, result_for(silence, text="Vielen Dank fürs Zuschauen"))
    gate = evaluate_gate(_candidate(silence=metrics), BASELINE)
    assert gate.failures == ("silence_insertions:silence",)
    assert gate.record()["passed"] is False


def test_wer_regression_beyond_tolerance_fails_and_within_passes():
    worse = evaluate_gate(_candidate(**{"de-speech": {"wer": 0.08}}), BASELINE)
    assert worse.failures == ("regression:de-speech:wer",)
    assert evaluate_gate(_candidate(**{"de-speech": {"wer": 0.07}}), BASELINE).passed
    cer = evaluate_gate(_candidate(**{"table-columns-pdf": {"cer": 0.04}}), BASELINE)
    assert cer.failures == ("regression:table-columns-pdf:cer",)


def test_fixture_missing_from_baseline_is_baseline_required():
    candidate = _candidate()
    candidate["de-numbers"] = {"wer": 0.0, "cer": 0.0}
    gate = evaluate_gate(candidate, BASELINE)
    assert gate.failures == ("baseline_required:de-numbers",)


def test_baseline_fixture_not_evaluated_fails():
    candidate = _candidate()
    del candidate["de-speech"]
    assert evaluate_gate(candidate, BASELINE).failures == ("missing_fixture:de-speech",)


def test_no_baseline_is_baseline_required_but_promotable_when_absolute_checks_pass(perfect_runs):
    gate = evaluate_gate(score_runs(perfect_runs), None)
    assert not gate.passed and gate.outcome == "baseline_required"
    assert gate.record() == {"passed": True, "failures": []}

    broken = score_runs(perfect_runs)
    broken["silence"]["silence_insertions"] = 2
    gate = evaluate_gate(broken, None)
    assert gate.outcome == "gate_failed"
    assert gate.record() == {"passed": False, "failures": ["silence_insertions:silence"]}
