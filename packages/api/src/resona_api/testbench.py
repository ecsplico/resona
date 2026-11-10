"""Testbench domain logic: candidate discovery, run admission, result capture and scoring.

A testbench item is a contributed recording or scan kept persistently under
``storage.testbench_dir``. A run is one job of one candidate over the item's bytes.
The collector copies a finished job's result into ``run-<id>.json`` before the
24-hour result retention (and the later job-row purge) can remove it, then records
seconds, device and, when the item has a reference text, WER/CER. Runs therefore
outlive their jobs. Nothing here runs inference: jobs wait for approved private
pull workers like any other job.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from resona_eval import metrics
from resona_job_core import Candidate, JobResult, OrganizationSettings
from sqlalchemy import exists, func
from sqlmodel import Session, select

from . import broker, clock, jobs_routes, storage
from . import engine_registry as reg
from .db.models import Job, JobStatus, Organization, TestbenchItem, TestbenchRun, Worker
from .errors import api_error
from .jobs_routes import CONTENT_UNAVAILABLE
from .settings_store import current_settings

log = logging.getLogger(__name__)

DRAFT = "draft"
BENCH = "bench"
ACTIVE = ("queued", "processing")
ITEM_STATUSES = ("awaiting_draft", "needs_correction", "corrected")
MAX_RUN_CANDIDATES = 16
MAX_REFERENCE_CHARS = 1_000_000
MAX_LABEL_CHARS = 120


# -- helpers --------------------------------------------------------------

def kind_of(candidate: Candidate) -> str:
    """Docling candidates run documents, every other engine runs audio."""
    return "document" if candidate.engine == "docling" else "audio"


def candidate_json(candidate: Candidate) -> str:
    return json.dumps(candidate.model_dump(mode="json"), sort_keys=True)


def storage_id_of(session: Session, organization_id: str) -> str:
    return session.get(Organization, organization_id).storage_id


def testbench_enabled(session: Session, organization_id: str) -> bool:
    try:
        _, settings = current_settings(session, organization_id)
    except LookupError:
        return False
    return settings.testbench_enabled


def _is_cloud(candidate: Candidate) -> bool:
    return reg.cloud_asr_endpoint_id(candidate.engine) is not None


# -- candidates -----------------------------------------------------------

def offered_candidates(session: Session, organization_id: str) -> list[dict[str, Any]]:
    """Tuples an approved, unrevoked private worker of the organization both is approved for and
    reported as tested (the same rule as ``/v1/engines``), without the execution-profile filter.

    Cloud engines are left out: testbench jobs are private and never egress. ``workers_online``
    counts the offering workers seen within the online window.
    """
    online = {w.worker_id for w in jobs_routes._online_workers(session, organization_id, clock.now())}
    workers = session.exec(
        select(Worker).where(
            Worker.organization_id == organization_id,
            Worker.approved.is_(True),
            Worker.approved_private.is_(True),
            Worker.revoked_at.is_(None),
        )
    ).all()
    offered: dict[Candidate, set[str]] = {}
    for worker in workers:
        approved = {Candidate.model_validate(c) for c in json.loads(worker.approved_capabilities or "[]")}
        tested = {Candidate.model_validate(c) for c in json.loads(worker.reported_capabilities or "[]")}
        for candidate in approved & tested:
            if not _is_cloud(candidate):
                offered.setdefault(candidate, set()).add(worker.id)
    ordered = sorted(
        offered,
        key=lambda c: (kind_of(c), c.engine, c.model, c.accelerator, c.ocr_engine or "", c.model_revision or ""),
    )
    return [
        {
            "kind": kind_of(candidate),
            "candidate": candidate.model_dump(mode="json"),
            "workers_online": len(offered[candidate] & online),
        }
        for candidate in ordered
    ]


def draft_candidate(settings: OrganizationSettings, kind: str, offered: list[dict[str, Any]]) -> Candidate:
    """The candidate of an item's draft run.

    The first available candidate of the kind (the default execution profile's order first),
    else the first private candidate of the default execution profile, else of any profile.
    """
    available = [Candidate.model_validate(e["candidate"]) for e in offered if e["kind"] == kind]
    default = settings.execution_profiles.get(settings.default_execution_profile)
    preferred = [c for c in (default.candidates(kind) if default is not None else []) if not _is_cloud(c)]
    for candidate in preferred:
        if candidate in available:
            return candidate
    if available:
        return available[0]
    if preferred:
        return preferred[0]
    for _, profile in sorted(settings.execution_profiles.items()):
        for candidate in profile.candidates(kind):
            if not _is_cloud(candidate):
                return candidate
    raise api_error(422, "no_candidates", "no private candidate is configured for this kind of content")


# -- runs -----------------------------------------------------------------

def queue_run(session: Session, item: TestbenchItem, candidate: Candidate, role: str) -> TestbenchRun:
    """Admit a testbench job for ``item`` pinned to ``candidate`` and record the run.

    Runs inside the caller's write transaction and does not commit.
    """
    storage_id = storage_id_of(session, item.organization_id)
    job = jobs_routes.admit_testbench_job(
        session,
        item.organization_id,
        kind=item.kind,
        language=item.language,
        candidate=candidate,
        source=storage.testbench_item_path(storage_id, item.id),
        sha256=item.sha256,
        media_type=item.content_type,
    )
    run = TestbenchRun(
        item_id=item.id,
        organization_id=item.organization_id,
        role=role,
        candidate=candidate_json(candidate),
        job_id=job.id,
        status="queued",
        created_at=clock.now(),
    )
    session.add(run)
    session.flush()
    return run


def purge_runs(session: Session, storage_id: str, runs: list[TestbenchRun]) -> list[Path]:
    """Delete ``runs`` and cancel their jobs that are still active, in the caller's write transaction.

    Returns the paths to remove after the commit: the runs' captured results and the
    directories of the cancelled jobs. Terminal jobs are left to retention.
    """
    leftovers: list[Path] = []
    for run in runs:
        job = session.get(Job, run.job_id) if run.job_id is not None else None
        if job is not None and job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
            leftovers.append(storage.job_dir(storage_id, job.id))
            broker.purge_job(session, job)
        leftovers.append(storage.testbench_run_path(storage_id, run.id))
        session.delete(run)
    # No relationship orders the unit of work: delete the runs before a caller deletes their item,
    # otherwise the item's ON DELETE CASCADE removes them first and the run DELETE matches nothing.
    session.flush()
    return leftovers


def remove_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            storage.remove_tree(path)
        else:
            storage.remove_file(path)


# -- result capture and scoring -------------------------------------------

def hypothesis_text(kind: str, result: JobResult) -> str:
    """The text a run is scored on: ASR text for audio, Markdown for documents."""
    return result.markdown if kind == "document" else result.raw_text


def load_run_result(storage_id: str, run: TestbenchRun) -> JobResult | None:
    """The result a completed run captured, or None when it has none or the file is unreadable."""
    if run.status != "completed":
        return None
    try:
        return JobResult.model_validate_json(storage.testbench_run_path(storage_id, run.id).read_bytes())
    except (OSError, ValueError):
        return None


def _score(item: TestbenchItem, run: TestbenchRun, result: JobResult | None) -> None:
    if item.reference_text is None or result is None:
        run.wer = run.cer = None
        return
    scored = metrics.score(item.reference_text, hypothesis_text(item.kind, result), markdown=item.kind == "document")
    run.wer, run.cer = scored["wer"], scored["cer"]


def rescore(session: Session, item: TestbenchItem, storage_id: str) -> None:
    """Score every completed run of ``item`` against its current reference (caller's write transaction)."""
    runs = session.exec(
        select(TestbenchRun).where(TestbenchRun.item_id == item.id, TestbenchRun.status == "completed")
    ).all()
    for run in runs:
        _score(item, run, load_run_result(storage_id, run))
        session.add(run)


def _primary_device(result: JobResult, kind: str) -> str | None:
    primary = "asr" if kind == "audio" else "layout"
    stage = next((s for s in result.provenance.stages if s.stage == primary), None)
    if stage is None and result.provenance.stages:
        stage = result.provenance.stages[0]
    return stage.accelerator if stage is not None else None


def _finish(run: TestbenchRun, status: str, code: str | None, completed_at) -> bool:
    run.status = status
    run.error_code = code
    run.completed_at = completed_at
    return True


def _capture(session: Session, run: TestbenchRun, job: Job, storage_id: str) -> bool:
    """Copy the completed job's result into the run's own file and fill its metrics."""
    now = clock.now()
    item = session.get(TestbenchItem, run.item_id)
    try:
        if job.error_code == CONTENT_UNAVAILABLE:
            raise OSError("result content was not restored")
        raw = storage.result_path(storage_id, job.id).read_bytes()
        result = JobResult.model_validate_json(raw)
    except (OSError, ValueError):
        return _finish(run, "failed", "result_unavailable", now)
    storage.write_bytes_atomic(storage.testbench_run_path(storage_id, run.id), raw)
    run.seconds = round(sum(stage.seconds for stage in result.provenance.stages), 3)
    run.device = _primary_device(result, item.kind)
    _score(item, run, result)
    return _finish(run, "completed", None, job.completed_at or now)


def _collect_one(session: Session, run: TestbenchRun) -> bool:
    """Bring one active run in line with its job; True when the row changed."""
    storage_id = storage_id_of(session, run.organization_id)
    job = session.get(Job, run.job_id) if run.job_id is not None else None
    if job is None:
        return _finish(run, "failed", "job_lost", clock.now())
    if job.error_code == broker.JOB_DELETED:
        return _finish(run, "cancelled", "cancelled", clock.now())
    if job.status == JobStatus.COMPLETED:
        return _capture(session, run, job, storage_id)
    if job.status == JobStatus.FAILED:
        return _finish(run, "failed", job.error_code or "failed", job.completed_at or clock.now())
    if job.status == JobStatus.CANCELLED:
        return _finish(run, "cancelled", job.error_code or "cancelled", job.completed_at or clock.now())
    wanted = "processing" if job.status == JobStatus.PROCESSING else "queued"
    if run.status == wanted:
        return False
    run.status = wanted
    return True


def collect(session: Session, *, organization_id: str | None = None, item_id: int | None = None) -> int:
    """Capture finished jobs of active runs (optionally of one organization or item).

    Each run is settled in its own write transaction. Cheap when nothing is active, so the
    read routes call it lazily and the maintenance pass calls it for everything. Returns the
    number of runs whose state changed.
    """
    query = select(TestbenchRun.id).where(TestbenchRun.status.in_(ACTIVE))
    if organization_id is not None:
        query = query.where(TestbenchRun.organization_id == organization_id)
    if item_id is not None:
        query = query.where(TestbenchRun.item_id == item_id)
    run_ids = list(session.exec(query).all())
    changed = 0
    for run_id in run_ids:
        with broker._immediate(session):
            run = session.get(TestbenchRun, run_id)
            if run is None or run.status not in ACTIVE:
                continue
            if _collect_one(session, run):
                session.add(run)
                changed += 1
    session.commit()
    return changed


# -- views ----------------------------------------------------------------

class Views:
    """Serializes items and runs; computes the queue context at most once per request."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self._session = session
        self._organization_id = organization_id
        self._ctx: jobs_routes.QueueContext | None = None
        self.storage_id = storage_id_of(session, organization_id)

    def _queue_reason(self, run: TestbenchRun) -> str | None:
        if run.status != "queued" or run.job_id is None:
            return None
        job = self._session.get(Job, run.job_id)
        if job is None:
            return None
        if self._ctx is None:
            self._ctx = jobs_routes._queue_context(self._session, self._organization_id, clock.now())
        return jobs_routes._queue_reason(job, self._ctx)

    def run_summary(self, run: TestbenchRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "role": run.role,
            "candidate": json.loads(run.candidate),
            "status": run.status,
            "queue_reason": self._queue_reason(run),
            "error_code": run.error_code,
            "seconds": run.seconds,
            "device": run.device,
            "wer": run.wer,
            "cer": run.cer,
            "created_at": jobs_routes._iso(run.created_at),
            "completed_at": jobs_routes._iso(run.completed_at),
        }

    def run_detail(self, item: TestbenchItem, run: TestbenchRun, *, alignment: bool = True) -> dict[str, Any]:
        out = self.run_summary(run)
        result = load_run_result(self.storage_id, run)
        text = hypothesis_text(item.kind, result) if result is not None else None
        out["text"] = text
        out["warnings"] = list(result.warnings) if result is not None else []
        if alignment:
            out["alignment"] = (
                metrics.word_alignment(item.reference_text, text, markdown=item.kind == "document")
                if text is not None and item.reference_text is not None
                else None
            )
        return out

    @staticmethod
    def _draft(runs: list[TestbenchRun]) -> TestbenchRun | None:
        return next((r for r in sorted(runs, key=lambda r: r.id) if r.role == DRAFT), None)

    def item_summary(self, item: TestbenchItem, runs: list[TestbenchRun]) -> dict[str, Any]:
        draft = self._draft(runs)
        if item.reference_text is not None:
            status = "corrected"
        elif draft is not None and draft.status == "completed":
            status = "needs_correction"
        else:
            status = "awaiting_draft"
        return {
            "id": item.id,
            "kind": item.kind,
            "language": item.language,
            "label": item.label,
            "content_type": item.content_type,
            "size": item.size,
            "created_by": item.created_by_credential_id,
            "status": status,
            "draft_status": draft.status if draft is not None else None,
            "run_count": len(runs),
            "created_at": jobs_routes._iso(item.created_at),
            "reference_set_at": jobs_routes._iso(item.reference_set_at),
        }

    def item_detail(self, item: TestbenchItem, runs: list[TestbenchRun]) -> dict[str, Any]:
        ordered = sorted(runs, key=lambda r: r.id)
        draft = self._draft(ordered)
        return {
            **self.item_summary(item, ordered),
            "sha256": item.sha256,
            "reference": item.reference_text,
            "draft": self.run_detail(item, draft, alignment=False) if draft is not None else None,
            "runs": [self.run_summary(r) for r in ordered],
        }


def item_runs(session: Session, item_ids: list[int]) -> dict[int, list[TestbenchRun]]:
    grouped: dict[int, list[TestbenchRun]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        for run in session.exec(select(TestbenchRun).where(TestbenchRun.item_id.in_(item_ids))).all():
            grouped[run.item_id].append(run)
    return grouped


def completed_draft():
    """SQL condition: the item's draft run has completed."""
    return exists().where(
        TestbenchRun.item_id == TestbenchItem.id,
        TestbenchRun.role == DRAFT,
        TestbenchRun.status == "completed",
    )


def counts(session: Session, organization_id: str) -> dict[str, int]:
    items = session.exec(
        select(func.count()).select_from(TestbenchItem).where(TestbenchItem.organization_id == organization_id)
    ).one()
    corrected = session.exec(
        select(func.count()).select_from(TestbenchItem).where(
            TestbenchItem.organization_id == organization_id, TestbenchItem.reference_text.is_not(None)
        )
    ).one()
    runs = session.exec(
        select(func.count()).select_from(TestbenchRun).where(TestbenchRun.organization_id == organization_id)
    ).one()
    return {"items": items, "corrected": corrected, "runs": runs}


def leaderboard(session: Session, organization_id: str) -> dict[str, Any]:
    """Mean WER/CER/seconds per candidate over completed bench runs on corrected items.

    Draft runs are excluded: the reference started as the draft's output, which favours it.
    A candidate counts once per item (its latest completed run).
    """
    corrected = {
        item.id: item
        for item in session.exec(
            select(TestbenchItem).where(
                TestbenchItem.organization_id == organization_id, TestbenchItem.reference_text.is_not(None)
            )
        ).all()
    }
    latest: dict[tuple[str, int], TestbenchRun] = {}
    if corrected:
        runs = session.exec(
            select(TestbenchRun)
            .where(
                TestbenchRun.organization_id == organization_id,
                TestbenchRun.role == BENCH,
                TestbenchRun.status == "completed",
                TestbenchRun.item_id.in_(list(corrected)),
            )
            .order_by(TestbenchRun.id)
        ).all()
        for run in runs:
            if run.wer is not None:
                latest[(run.candidate, run.item_id)] = run
    grouped: dict[str, list[TestbenchRun]] = {}
    for (candidate, _), run in latest.items():
        grouped.setdefault(candidate, []).append(run)

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 6) if values else None

    rows = []
    for candidate, runs in grouped.items():
        parsed = Candidate.model_validate_json(candidate)
        rows.append({
            "kind": kind_of(parsed),
            "candidate": json.loads(candidate),
            "items": len(runs),
            "wer_mean": mean([r.wer for r in runs if r.wer is not None]),
            "cer_mean": mean([r.cer for r in runs if r.cer is not None]),
            "seconds_mean": mean([r.seconds for r in runs if r.seconds is not None]),
        })
    rows.sort(key=lambda r: (r["wer_mean"] is None, r["wer_mean"] if r["wer_mean"] is not None else 0.0,
                             r["kind"], json.dumps(r["candidate"], sort_keys=True)))
    return {"corrected_items": len(corrected), "items": rows}


__all__ = [
    "ACTIVE",
    "BENCH",
    "DRAFT",
    "ITEM_STATUSES",
    "MAX_LABEL_CHARS",
    "MAX_REFERENCE_CHARS",
    "MAX_RUN_CANDIDATES",
    "Views",
    "candidate_json",
    "collect",
    "completed_draft",
    "counts",
    "draft_candidate",
    "item_runs",
    "kind_of",
    "leaderboard",
    "load_run_result",
    "offered_candidates",
    "purge_runs",
    "queue_run",
    "remove_paths",
    "rescore",
    "storage_id_of",
    "testbench_enabled",
]
