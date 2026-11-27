"""Cleanup cases: loading, substring checks, judge verdicts and the measurement-only report."""
import json
from pathlib import Path

import pytest
from resona_eval.cleanup import (
    CleanupCase,
    CleanupCaseError,
    cases_sha256,
    cleanup_report,
    judge_document,
    judge_verdict,
    load_cleanup_cases,
    substring_checks,
)
from resona_job_core import MAX_CONTEXT_CHARS

SHIPPED = Path(__file__).resolve().parents[3] / "eval" / "cleanup" / "cases.json"
CASE = {"id": "de-x", "transcript": "am freitag nein streich das am montag", "before": "Notiz. ",
        "rubric": "Montag, kein Freitag.", "includes": ["montag"], "excludes": ["freitag", " äh "]}


def _write(tmp_path, cases) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_valid_file_loads(tmp_path):
    cases = load_cleanup_cases(_write(tmp_path, [CASE, {**CASE, "id": "de-y", "before": ""}]))
    assert [c.id for c in cases] == ["de-x", "de-y"]
    assert cases[0].context() == {"before": "Notiz. "}
    assert cases[1].context() is None


def test_shipped_cases_load_and_cover_the_ticket():
    cases = load_cleanup_cases(SHIPPED)
    assert len(cases) >= 6
    assert any(c.selection for c in cases)
    assert any(c.before and c.after and not c.selection for c in cases)


@pytest.mark.parametrize(("cases", "message"), [
    ([CASE, CASE], "case de-x: duplicate id"),
    ([{**CASE, "rubric": "  "}], "case de-x: rubric: Value error, must not be empty"),
    ([{**CASE, "transcript": ""}], "case de-x: transcript"),
    ([{**CASE, "after": "x" * (MAX_CONTEXT_CHARS + 1)}], "case de-x: after"),
    ([{**CASE, "colour": "red"}], "case de-x: colour"),
    ([], "non-empty 'cases' list"),
])
def test_invalid_cases_are_rejected_naming_the_case(tmp_path, cases, message):
    with pytest.raises(CleanupCaseError, match=message):
        load_cleanup_cases(_write(tmp_path, cases))


def test_substring_checks_ignore_case_and_punctuation():
    case = CleanupCase.model_validate({**CASE, "includes": ["montag", "2,5 mg"]})
    assert substring_checks(case, "Am Montag, 2,5 mg.") == ([], [])
    assert substring_checks(case, "Äh, am Freitag.") == (["montag", "2,5 mg"], ["freitag", " äh "])
    assert substring_checks(case, "Ähnlich am Montag 2,5 mg")[1] == []  # " äh " is a whole word only


def test_judge_document_labels_every_part():
    doc = judge_document(CleanupCase.model_validate(CASE), "Am Montag.")
    for part in ("=== RUBRIK ===\nMontag, kein Freitag.", "=== TEXT VOR DER EINFÜGESTELLE ===\nNotiz. ",
                 "=== TEXT NACH DER EINFÜGESTELLE ===\n(leer)", "=== ROHES TRANSKRIPT ===\nam freitag",
                 "=== BEREINIGTES ERGEBNIS ===\nAm Montag."):
        assert part in doc


@pytest.mark.parametrize(("structured", "expected"), [
    ({"judge": {"pass": True, "reason": "ok"}}, {"pass": True, "reason": "ok"}),
    ({"judge": {"pass": "yes", "reason": "ok"}}, None),
    ({"judge": {"_raw": "kein json"}}, None),
    ({}, None),
    (None, None),
])
def test_judge_verdict(structured, expected):
    verdict = judge_verdict(structured)
    if expected is None:
        assert verdict["pass"] is False and verdict["reason"].startswith("judge output invalid: ")
    else:
        assert verdict == expected


def test_cleanup_report_is_measurement_only(tmp_path):
    path = _write(tmp_path, [CASE, {**CASE, "id": "de-y"}, {**CASE, "id": "de-z"}])
    cases = load_cleanup_cases(path)
    report = cleanup_report(
        cases=cases,
        outputs={"de-x": "Am Montag.", "de-y": "Am Freitag.", "de-z": "Am Montag."},
        verdicts={"de-x": {"pass": True, "reason": "ok"}, "de-y": {"pass": True, "reason": "ok"},
                  "de-z": {"pass": False, "reason": "falsch"}},
        profile="dictation-clean-de", judge_profile="cleanup-judge-de", cases_sha256=cases_sha256(path),
    )
    assert report["kind"] == "cleanup" and report["outcome"] == "measurement_only"
    assert report["cases_sha256"] == cases_sha256(path) and len(report["cases_sha256"]) == 64
    assert (report["passed"], report["total"]) == (1, 3)
    by_id = {entry["id"]: entry for entry in report["cases"]}
    assert by_id["de-x"]["pass"] is True
    assert by_id["de-y"]["missing_includes"] == ["montag"]
    assert by_id["de-y"]["present_excludes"] == ["freitag"]
    assert by_id["de-y"]["pass"] is False
    assert by_id["de-z"]["judge"] == {"pass": False, "reason": "falsch"} and by_id["de-z"]["pass"] is False
