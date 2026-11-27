"""Dictation-cleanup evaluation: cases, the judge's input document and the measurement-only report.

A case is a raw dictation ``transcript`` with the text around its insertion point
(``before``/``selection``/``after``, sent as the job ``context``), a ``rubric`` a judge
model reads, and optional ``includes``/``excludes``: case-insensitive substrings a usable
answer must (not) contain, a mechanical floor under the rubric. Case shape adapted from
T3 Code's ``evals/voice-cleanup`` (pingdotgg/t3code, MIT).

The run itself (cleanup job, then a judge job on the ``@judge`` role) lives in
``resona eval cleanup``; this module stays pure: no HTTP, no models.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from resona_job_core import MAX_CONTEXT_CHARS

CLEANUP_REPORT_KIND = "cleanup"
JUDGE_STEP = "judge"
_PUNCTUATION = re.compile(r"[.,;:!?„“”\"'()]")


class CleanupCaseError(ValueError):
    """The cases file is unreadable or a case is invalid; the message names the case."""


class CleanupCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    transcript: str
    before: str = Field(default="", max_length=MAX_CONTEXT_CHARS)
    selection: str = Field(default="", max_length=MAX_CONTEXT_CHARS)
    after: str = Field(default="", max_length=MAX_CONTEXT_CHARS)
    rubric: str
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)

    @field_validator("transcript", "rubric")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    def context(self) -> Optional[dict[str, str]]:
        """The job ``context`` (parts with text only), ``None`` when the case has none."""
        parts = {key: value for key in ("before", "selection", "after") if (value := getattr(self, key)).strip()}
        return parts or None


def load_cleanup_cases(path: Path | str) -> list[CleanupCase]:
    """Cases of a cases file (``{"cases": [...]}``); ids unique, each case validated."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CleanupCaseError(f"cannot read {path}: {e}") from None
    raw_cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CleanupCaseError(f"{path}: expected an object with a non-empty 'cases' list")
    cases: list[CleanupCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        label = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else f"#{index}"
        try:
            case = CleanupCase.model_validate(raw)
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'case'}: {err['msg']}" for err in e.errors())
            raise CleanupCaseError(f"{path}: case {label}: {problems}") from None
        if case.id in seen:
            raise CleanupCaseError(f"{path}: case {case.id}: duplicate id")
        seen.add(case.id)
        cases.append(case)
    return cases


def cases_sha256(path: Path | str) -> str:
    """SHA-256 of the cases file: a report names exactly the cases it ran."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", _PUNCTUATION.sub(" ", text.lower()))


def substring_checks(case: CleanupCase, output: str) -> tuple[list[str], list[str]]:
    """``(missing includes, present excludes)``. Punctuation counts as a space on both sides and the
    output is padded with spaces, so an exclude written as ``" äh "`` matches whole words only."""
    text = f" {_normalized(output)} "
    missing = [needle for needle in case.includes if _normalized(needle) not in text]
    present = [needle for needle in case.excludes if _normalized(needle) in text]
    return missing, present


def judge_document(case: CleanupCase, output: str) -> str:
    """The judge job's input: rubric, insertion context, raw transcript and the cleaned result, labelled."""
    def block(label: str, text: str) -> str:
        return f"=== {label} ===\n{text if text.strip() else '(leer)'}\n"

    return "\n".join([
        block("RUBRIK", case.rubric),
        block("TEXT VOR DER EINFÜGESTELLE", case.before),
        block("AUSWAHL (wird durch das Ergebnis ersetzt)", case.selection),
        block("TEXT NACH DER EINFÜGESTELLE", case.after),
        block("ROHES TRANSKRIPT", case.transcript),
        block("BEREINIGTES ERGEBNIS", output),
    ])


def judge_verdict(structured: Optional[dict[str, Any]]) -> dict[str, Any]:
    """``{pass, reason}`` from a judge job's ``structured``; anything malformed fails the case."""
    verdict = (structured or {}).get(JUDGE_STEP)
    if not isinstance(verdict, dict):
        return {"pass": False, "reason": f"judge output invalid: no '{JUDGE_STEP}' object"}
    if "_raw" in verdict:
        return {"pass": False, "reason": f"judge output invalid: not JSON: {str(verdict['_raw'])[:200]}"}
    passed, reason = verdict.get("pass"), verdict.get("reason")
    if not isinstance(passed, bool) or not isinstance(reason, str):
        return {"pass": False, "reason": f"judge output invalid: expected {{pass: bool, reason: str}}, got "
                                         f"{json.dumps(verdict, ensure_ascii=False)[:200]}"}
    return {"pass": passed, "reason": reason}


def cleanup_report(
    *,
    cases: list[CleanupCase],
    outputs: dict[str, str],
    verdicts: dict[str, dict[str, Any]],
    profile: str,
    judge_profile: str,
    cases_sha256: str,
) -> dict[str, Any]:
    """Measurement-only report: per case the cleaned output, the judge's verdict and the substring
    checks. A case passes when the judge passes it and no include is missing nor exclude present."""
    entries = []
    for case in cases:
        output = outputs[case.id]
        verdict = verdicts[case.id]
        missing, present = substring_checks(case, output)
        entries.append({
            "id": case.id,
            "output": output,
            "judge": {"pass": bool(verdict["pass"]), "reason": str(verdict["reason"])},
            "missing_includes": missing,
            "present_excludes": present,
            "pass": bool(verdict["pass"]) and not missing and not present,
        })
    return {
        "kind": CLEANUP_REPORT_KIND,
        "outcome": "measurement_only",
        "profile": profile,
        "judge_profile": judge_profile,
        "cases_sha256": cases_sha256,
        "cases": entries,
        "passed": sum(entry["pass"] for entry in entries),
        "total": len(entries),
    }
