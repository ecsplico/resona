"""Central quality control: evaluation summaries, baselines, promotion, and the hub version.

Evaluations hold metrics and provenance only (per-fixture WER/CER and absolute
check counts, corpus hash, release id); no transcript, Markdown or other text
payload is accepted. Every route except ``GET /v1/version`` requires an
``org_admin`` credential and is scoped to its organization; other
organizations' evaluations answer 404.

Promoting an evaluation makes it the next baseline revision of its
``(execution_profile, platform)`` and approves its release for production jobs
of that tuple. The hub re-checks the gate itself: the first baseline (or one on
a changed corpus) needs zero silence insertions, missing cells and order
failures; later baselines additionally need every baseline fixture and metric
present, no WER/CER/DER regression above :data:`REGRESSION_LIMIT` and no
speaker attribution more than that limit below the baseline. The speaker count
error of diarize fixtures is reported, never gated.

Every recorded fixture declares its kind and corpus checks, and must carry
exactly the metrics those checks produce, so an empty metrics object cannot
pass the absolute checks. Promoting onto a corpus other than the current
baseline's is refused (``baseline_corpus_mismatch``) unless the admin asks for
``rebaseline``; that first baseline of the new corpus passes on absolute checks.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, ValidationError
from sqlmodel import Session, select

from . import clock
from .auth import Principal, admin_principal
from .broker import PROTOCOL_VERSION, _immediate
from .db.models import ApprovedReleaseTuple, Baseline, Evaluation, Release
from .deps import get_db_session
from .errors import api_error, validation_message
from .releases import hub_release_id, hub_version, is_known

router = APIRouter(tags=["Evaluations"])

REGRESSION_LIMIT = 0.02
ABSOLUTE_CHECKS = ("silence_insertions", "missing_cells", "order_failures")
# Lower is better; a value above baseline + limit is a regression.
RATE_METRICS = ("wer", "cer", "der")
# Higher is better; a value below baseline - limit is a regression.
SCORE_METRICS = ("speaker_attribution",)
# Reported only: must be present when the baseline has it, never compared.
REPORTED_METRICS = ("speaker_count_error",)
METRIC_OF_CHECK = {
    "wer": "wer",
    "cer": "cer",
    "silence": "silence_insertions",
    "table_cells": "missing_cells",
    "reading_order": "order_failures",
    "speaker_attribution": "speaker_attribution",
    "der": "der",
    "speaker_count": "speaker_count_error",
}
CHECKS_OF_KIND = {
    "audio": frozenset({"wer", "cer", "silence", "speaker_attribution", "der", "speaker_count"}),
    "document": frozenset({"cer", "table_cells", "reading_order"}),
}
MAX_BODY_BYTES = 1024 * 1024
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_FIXTURE_ID = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
# Tolerance for float noise when comparing rates against baseline + limit.
_EPSILON = 1e-9


class FixtureMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    wer: Optional[float] = Field(default=None, ge=0)
    cer: Optional[float] = Field(default=None, ge=0)
    silence_insertions: Optional[StrictInt] = Field(default=None, ge=0)
    missing_cells: Optional[StrictInt] = Field(default=None, ge=0)
    order_failures: Optional[StrictInt] = Field(default=None, ge=0)
    speaker_attribution: Optional[float] = Field(default=None, ge=0, le=1)
    der: Optional[float] = Field(default=None, ge=0)
    speaker_count_error: Optional[StrictInt] = Field(default=None, ge=0)


Check = Literal[
    "wer", "cer", "silence", "table_cells", "reading_order", "speaker_attribution", "der", "speaker_count"
]


class FixtureChecks(BaseModel):
    """What the corpus manifest declares for one fixture."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["audio", "document"]
    checks: list[Check] = Field(min_length=1, max_length=len(METRIC_OF_CHECK))


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    passed: StrictBool
    failures: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=200)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(pattern=_NAME)
    execution_profile: str = Field(pattern=_NAME)
    platform: Literal["mac-metal", "linux-cuda", "cpu"]
    release_id: str = Field(pattern=r"^[0-9][0-9A-Za-z.+-]{0,63}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_revision: Optional[StrictInt] = Field(default=None, ge=1)
    fixtures: dict[Annotated[str, StringConstraints(pattern=_FIXTURE_ID)], FixtureMetrics] = Field(
        min_length=1, max_length=500
    )
    fixture_checks: dict[Annotated[str, StringConstraints(pattern=_FIXTURE_ID)], FixtureChecks] = Field(
        min_length=1, max_length=500
    )
    gate: Gate


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_baseline_revision: Optional[StrictInt] = Field(...)
    rebaseline: StrictBool = False


# ── gate (pure) ─────────────────────────────────────────────────────────────

def completeness_failures(
    fixtures: dict[str, dict[str, Any]], checks: dict[str, FixtureChecks]
) -> list[str]:
    """Fixtures whose metrics are not exactly what their declared kind and checks produce."""
    failures = []
    for fixture_id in sorted(set(fixtures) | set(checks)):
        declared = checks.get(fixture_id)
        if declared is None:
            failures.append(f"{fixture_id}: checks missing")
            continue
        if fixture_id not in fixtures:
            failures.append(f"{fixture_id}: metrics missing")
            continue
        invalid = sorted(set(declared.checks) - CHECKS_OF_KIND[declared.kind])
        if invalid:
            failures.append(f"{fixture_id}: {', '.join(invalid)} not a {declared.kind} check")
            continue
        required = {METRIC_OF_CHECK[check] for check in declared.checks}
        present = set(fixtures[fixture_id])
        for metric in sorted(required - present):
            failures.append(f"{fixture_id}: {metric} missing")
        for metric in sorted(present - required):
            failures.append(f"{fixture_id}: {metric} not declared")
    return failures


def absolute_failures(fixtures: dict[str, dict[str, Any]]) -> list[str]:
    """Fixtures with any silence insertion, missing table cell or reading-order failure."""
    failures = []
    for fixture_id in sorted(fixtures):
        for check in ABSOLUTE_CHECKS:
            count = fixtures[fixture_id].get(check)
            if count:
                failures.append(f"{fixture_id}: {check}={count}")
    return failures


def regression_failures(
    fixtures: dict[str, dict[str, Any]], baseline: dict[str, dict[str, Any]]
) -> list[str]:
    """Missing baseline fixtures/metrics, WER/CER/DER regressions above the limit and
    speaker attribution drops below it."""
    failures = []
    for fixture_id in sorted(baseline):
        candidate = fixtures.get(fixture_id)
        if candidate is None:
            failures.append(f"{fixture_id}: baseline_required (fixture missing from evaluation)")
            continue
        for metric in (*RATE_METRICS, *SCORE_METRICS, *ABSOLUTE_CHECKS, *REPORTED_METRICS):
            reference = baseline[fixture_id].get(metric)
            if reference is None:
                continue
            value = candidate.get(metric)
            if value is None:
                failures.append(f"{fixture_id}: {metric} missing")
            elif metric in RATE_METRICS and value > reference + REGRESSION_LIMIT + _EPSILON:
                failures.append(f"{fixture_id}: {metric} {value:.4f} > baseline {reference:.4f} + {REGRESSION_LIMIT}")
            elif metric in SCORE_METRICS and value < reference - REGRESSION_LIMIT - _EPSILON:
                failures.append(f"{fixture_id}: {metric} {value:.4f} < baseline {reference:.4f} - {REGRESSION_LIMIT}")
    return failures


# ── helpers ─────────────────────────────────────────────────────────────────

async def _json_body(request: Request, model: type[BaseModel]) -> Any:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise api_error(413, "too_large", "request body is too large")
    try:
        return model.model_validate_json(raw or b"null")
    except ValidationError as exc:
        raise api_error(422, "invalid_request", validation_message(exc)) from None
    except ValueError:
        raise api_error(422, "invalid_request", "body must be JSON") from None


def _owned(session: Session, organization_id: str, evaluation_id: int) -> Evaluation:
    evaluation = session.get(Evaluation, evaluation_id)
    if evaluation is None or evaluation.organization_id != organization_id:
        raise api_error(404, "evaluation_not_found", "evaluation not found")
    return evaluation


def _view(evaluation: Evaluation) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation.id,
        "status": "promoted" if evaluation.promoted_revision is not None else "recorded",
        "profile": evaluation.profile,
        "execution_profile": evaluation.execution_profile,
        "platform": evaluation.platform,
        "release_id": evaluation.release_id,
        "corpus_sha256": evaluation.corpus_sha256,
        "baseline_revision": evaluation.baseline_revision,
        "fixtures": json.loads(evaluation.fixtures_json),
        "gate": json.loads(evaluation.gate_json),
        "promoted_revision": evaluation.promoted_revision,
        "created_at": clock.as_utc(evaluation.created_at).isoformat(),
    }


def _current_baseline(
    session: Session, organization_id: str, execution_profile: str, platform: str
) -> Baseline | None:
    return session.exec(
        select(Baseline)
        .where(
            Baseline.organization_id == organization_id,
            Baseline.execution_profile == execution_profile,
            Baseline.platform == platform,
        )
        .order_by(Baseline.revision.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/v1/version")
def version() -> dict[str, Any]:
    """Release identifiers and the worker protocol major. No authentication."""
    return {"release_id": hub_release_id(), "hub_version": hub_version(), "worker_protocol": PROTOCOL_VERSION}


@router.post("/v1/evaluations", status_code=201)
async def create_evaluation(
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
) -> JSONResponse:
    """Record an evaluation summary (metrics only) for a staged or bundled release."""
    body: EvaluationRequest = await _json_body(request, EvaluationRequest)
    if not is_known(session, body.release_id):
        raise api_error(422, "release_not_staged", "release is not staged on this hub")
    fixtures = {key: value.model_dump(exclude_none=True) for key, value in body.fixtures.items()}
    incomplete = completeness_failures(fixtures, body.fixture_checks)
    if incomplete:
        raise api_error(422, "incomplete_metrics", "; ".join(incomplete)[:500])
    evaluation = Evaluation(
        organization_id=principal.organization_id,
        profile=body.profile,
        execution_profile=body.execution_profile,
        platform=body.platform,
        release_id=body.release_id,
        corpus_sha256=body.corpus_sha256,
        baseline_revision=body.baseline_revision,
        fixtures_json=json.dumps(fixtures, sort_keys=True),
        gate_json=body.gate.model_dump_json(),
        created_at=clock.now(),
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return JSONResponse(_view(evaluation), status_code=201)


@router.get("/v1/evaluations/{evaluation_id}")
def get_evaluation(
    evaluation_id: int,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _view(_owned(session, principal.organization_id, evaluation_id))


@router.get("/v1/baselines")
def get_baseline(
    execution_profile: str = Query(..., pattern=_NAME),
    platform: Literal["mac-metal", "linux-cuda", "cpu"] = Query(...),
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The current baseline of one (execution profile, platform); 404 ``baseline_required`` if none."""
    baseline = _current_baseline(session, principal.organization_id, execution_profile, platform)
    if baseline is None:
        raise api_error(404, "baseline_required", "no baseline has been promoted for this profile and platform")
    evaluation = session.get(Evaluation, baseline.evaluation_id)
    return {
        "revision": baseline.revision,
        "evaluation_id": evaluation.id,
        "execution_profile": execution_profile,
        "platform": platform,
        "corpus_sha256": evaluation.corpus_sha256,
        "release_id": evaluation.release_id,
        "fixtures": json.loads(evaluation.fixtures_json),
    }


@router.post("/v1/evaluations/{evaluation_id}/promote")
async def promote_evaluation(
    evaluation_id: int,
    request: Request,
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Make a passing evaluation the next baseline and approve its release for the tuple."""
    body: PromoteRequest = await _json_body(request, PromoteRequest)
    org = principal.organization_id
    now = clock.now()
    with _immediate(session):
        evaluation = _owned(session, org, evaluation_id)
        if evaluation.promoted_revision is not None:
            raise api_error(409, "already_promoted", "evaluation is already a baseline")
        gate = json.loads(evaluation.gate_json)
        if not gate.get("passed"):
            raise api_error(422, "gate_failed", "evaluation did not pass its quality gate")
        current = _current_baseline(session, org, evaluation.execution_profile, evaluation.platform)
        current_revision = current.revision if current is not None else None
        if body.expected_baseline_revision != current_revision:
            raise api_error(
                409, "baseline_revision_mismatch",
                f"current baseline revision is {current_revision}",
            )
        if session.get(Release, evaluation.release_id) is None:
            raise api_error(422, "release_not_staged", "release is not staged on this hub")

        fixtures = json.loads(evaluation.fixtures_json)
        empty = sorted(fixture_id for fixture_id, metrics in fixtures.items() if not metrics)
        if empty:
            raise api_error(422, "incomplete_metrics", ("no metrics: " + ", ".join(empty))[:500])
        absolute = absolute_failures(fixtures)
        if absolute:
            raise api_error(422, "gate_failed", "; ".join(absolute)[:500])
        if current is not None:
            reference = session.get(Evaluation, current.evaluation_id)
            if reference.corpus_sha256 == evaluation.corpus_sha256:
                regressions = regression_failures(fixtures, json.loads(reference.fixtures_json))
                if regressions:
                    raise api_error(422, "gate_failed", "; ".join(regressions)[:500])
            elif not body.rebaseline:
                raise api_error(
                    409, "baseline_corpus_mismatch",
                    "current baseline was measured on another corpus; promote with rebaseline to start a new one",
                )

        revision = (current_revision or 0) + 1
        session.add(Baseline(
            organization_id=org,
            execution_profile=evaluation.execution_profile,
            platform=evaluation.platform,
            revision=revision,
            evaluation_id=evaluation.id,
            created_at=now,
        ))
        evaluation.promoted_revision = revision
        session.add(evaluation)
        key = (org, evaluation.execution_profile, evaluation.platform, evaluation.release_id)
        approval = session.get(ApprovedReleaseTuple, key)
        if approval is None:
            approval = ApprovedReleaseTuple(
                organization_id=org,
                execution_profile=evaluation.execution_profile,
                platform=evaluation.platform,
                release_id=evaluation.release_id,
                evaluation_id=evaluation.id,
                approved_at=now,
            )
        else:
            approval.evaluation_id = evaluation.id
            approval.approved_at = now
        session.add(approval)
    return {
        "baseline_revision": revision,
        "evaluation_id": evaluation_id,
        "execution_profile": key[1],
        "platform": key[2],
        "release_id": key[3],
    }


__all__ = ["REGRESSION_LIMIT", "absolute_failures", "completeness_failures", "regression_failures", "router"]
