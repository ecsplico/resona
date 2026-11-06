"""Testbench routes under ``/v1/testbench``: contribute recordings and scans, correct the
draft into a reference, bench candidates against it and rank them.

An organization opts in with ``testbench_enabled`` in its settings; until then every route
except ``/status`` answers 403 ``testbench_disabled``. Contributing needs a ``client`` or
``org_admin`` credential, everything else an ``org_admin`` one; the hub operator never
reaches these routes because it never reads job content. Runs are ordinary private jobs of
purpose ``testbench`` (see ``jobs_routes.admit_testbench_job``) that approved pull workers
claim; the hub runs no inference. Other organizations' IDs are 404.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from resona_job_core import Candidate
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from . import clock, jobs_routes, storage, testbench
from .auth import Principal, admin_principal, client_principal
from .broker import _immediate
from .db.engine import engine
from .db.models import TestbenchItem, TestbenchRun, Upload
from .deps import get_db_session
from .errors import api_error
from .settings_store import current_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/testbench", tags=["Testbench"])

_LANGUAGE = re.compile(r"^[a-z]{2,3}$")
DISABLED_MESSAGE = (
    "testbench is disabled for this organization; an org admin can enable it in the admin UI "
    "(Testbench tab) or by setting testbench_enabled in the organization settings"
)


# -- request models and access control ------------------------------------

class ReferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=testbench.MAX_REFERENCE_CHARS)


class RunsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate] = Field(min_length=1, max_length=testbench.MAX_RUN_CANDIDATES)


def _require_enabled(session: Session, organization_id: str) -> None:
    if not testbench.testbench_enabled(session, organization_id):
        raise api_error(403, "testbench_disabled", DISABLED_MESSAGE)


def contributor(
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> Principal:
    """``client`` or ``org_admin`` of an organization that enabled the testbench."""
    _require_enabled(session, principal.organization_id)
    return principal


def curator(
    principal: Principal = Depends(admin_principal),
    session: Session = Depends(get_db_session),
) -> Principal:
    """``org_admin`` of an organization that enabled the testbench."""
    _require_enabled(session, principal.organization_id)
    return principal


def _owned_item(session: Session, organization_id: str, item_id: int) -> TestbenchItem:
    item = session.get(TestbenchItem, item_id)
    if item is None or item.organization_id != organization_id:
        raise api_error(404, "item_not_found", "testbench item not found")
    return item


def _owned_run(session: Session, item: TestbenchItem, run_id: int) -> TestbenchRun:
    run = session.get(TestbenchRun, run_id)
    if run is None or run.item_id != item.id:
        raise api_error(404, "run_not_found", "testbench run not found")
    return run


def _item_runs(session: Session, item: TestbenchItem) -> list[TestbenchRun]:
    return testbench.item_runs(session, [item.id])[item.id]


def _item_detail(session: Session, item: TestbenchItem) -> dict[str, Any]:
    views = testbench.Views(session, item.organization_id)
    return views.item_detail(item, _item_runs(session, item))


# -- status and candidates ------------------------------------------------

@router.get("/status")
def get_status(
    principal: Principal = Depends(client_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Whether the organization enabled the testbench, with how much it holds."""
    enabled = testbench.testbench_enabled(session, principal.organization_id)
    if not enabled:
        return {"enabled": False, "items": 0, "corrected": 0, "runs": 0}
    return {"enabled": True, **testbench.counts(session, principal.organization_id)}


@router.get("/candidates")
def list_candidates(
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Tuples an approved private worker has tested: what a bench run may pick from."""
    return {"items": testbench.offered_candidates(session, principal.organization_id)}


# -- items ----------------------------------------------------------------

def _contribute(organization_id: str, credential_id: str, upload_id: str, fields: dict[str, str]) -> dict[str, Any]:
    """Turn the streamed upload into a persistent item and queue its draft run, atomically."""
    language = (fields.get("language") or "").strip()
    label = (fields.get("label") or "").strip()
    if len(label) > testbench.MAX_LABEL_CHARS:
        raise api_error(422, "invalid_label", f"label must be at most {testbench.MAX_LABEL_CHARS} characters")
    if language and not _LANGUAGE.fullmatch(language):
        raise api_error(422, "invalid_language", "language must be a lowercase ISO 639 code")
    moved: tuple[Any, Any] | None = None
    with Session(engine) as session:
        try:
            with _immediate(session):
                upload = session.get(Upload, upload_id)
                if upload is None or upload.organization_id != organization_id or upload.consumed_by_job_id is not None:
                    raise api_error(404, "upload_not_found", "upload not found")
                try:
                    _, settings = current_settings(session, organization_id)
                except LookupError:
                    raise api_error(409, "settings_missing", "organization has no settings") from None
                item = TestbenchItem(
                    organization_id=organization_id,
                    kind=upload.media_family,
                    language=language or settings.language,
                    label=label,
                    content_type=upload.media_type,
                    size=upload.size,
                    sha256=upload.sha256,
                    created_by_credential_id=credential_id,
                    created_at=clock.now(),
                )
                session.add(item)
                session.flush()
                storage_id = testbench.storage_id_of(session, organization_id)
                src = storage.upload_path(storage_id, upload.id)
                dst = storage.testbench_item_path(storage_id, item.id)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(src, dst)
                except FileNotFoundError:
                    raise api_error(404, "upload_not_found", "upload not found") from None
                moved = (src, dst)
                session.delete(upload)
                offered = testbench.offered_candidates(session, organization_id)
                candidate = testbench.draft_candidate(settings, item.kind, offered)
                testbench.queue_run(session, item, candidate, testbench.DRAFT)
                item_id = item.id
        except BaseException:
            if moved is not None and moved[1].exists():
                os.replace(moved[1], moved[0])
            raise
        item = session.get(TestbenchItem, item_id)
        detail = _item_detail(session, item)
    logger.info("Contributed %s testbench item %s", detail["kind"], item_id)
    return detail


@router.post("/items", status_code=201)
async def create_item(
    request: Request,
    principal: Principal = Depends(contributor),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Store a recording or scan persistently (multipart ``file``, optional ``language``/``label``)
    and queue its draft run."""
    stored, fields = await jobs_routes.receive_upload(request, session, principal.organization_id)
    try:
        return await run_in_threadpool(
            _contribute, principal.organization_id, principal.credential_id, stored["upload_id"], fields
        )
    except BaseException:
        await run_in_threadpool(jobs_routes.discard_upload, principal.organization_id, stored["upload_id"])
        raise


@router.get("/items")
def list_items(
    kind: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = jobs_routes.DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """The organization's items, newest first, with an opaque cursor."""
    if not 1 <= limit <= jobs_routes.MAX_PAGE_SIZE:
        raise api_error(422, "invalid_limit", f"limit must be between 1 and {jobs_routes.MAX_PAGE_SIZE}")
    if kind is not None and kind not in ("audio", "document"):
        raise api_error(422, "invalid_kind", "kind must be audio or document")
    if status_filter is not None and status_filter not in testbench.ITEM_STATUSES:
        raise api_error(422, "invalid_status", f"status must be one of {', '.join(testbench.ITEM_STATUSES)}")
    testbench.collect(session, organization_id=principal.organization_id)
    query = select(TestbenchItem).where(TestbenchItem.organization_id == principal.organization_id)
    if kind is not None:
        query = query.where(TestbenchItem.kind == kind)
    if status_filter == "corrected":
        query = query.where(TestbenchItem.reference_text.is_not(None))
    elif status_filter == "needs_correction":
        query = query.where(TestbenchItem.reference_text.is_(None), testbench.completed_draft())
    elif status_filter == "awaiting_draft":
        query = query.where(TestbenchItem.reference_text.is_(None), ~testbench.completed_draft())
    if cursor is not None:
        query = query.where(TestbenchItem.id < jobs_routes._decode_cursor(cursor))
    rows = session.exec(query.order_by(TestbenchItem.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    runs = testbench.item_runs(session, [item.id for item in page])
    views = testbench.Views(session, principal.organization_id)
    return {
        "items": [views.item_summary(item, runs[item.id]) for item in page],
        "next_cursor": jobs_routes._encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


@router.get("/items/{item_id}")
def get_item(
    item_id: int,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    item = _owned_item(session, principal.organization_id, item_id)
    testbench.collect(session, organization_id=principal.organization_id, item_id=item.id)
    session.refresh(item)
    return _item_detail(session, item)


@router.get("/items/{item_id}/file")
def get_item_file(
    item_id: int,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> FileResponse:
    """The original bytes, served inline with the stored media type."""
    item = _owned_item(session, principal.organization_id, item_id)
    path = storage.testbench_item_path(testbench.storage_id_of(session, item.organization_id), item.id)
    if not path.is_file():
        raise api_error(410, jobs_routes.CONTENT_UNAVAILABLE, "stored content is unavailable")
    return FileResponse(
        path,
        media_type=item.content_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.put("/items/{item_id}/reference")
def put_reference(
    item_id: int,
    body: ReferenceBody,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Save the corrected text as the item's reference and re-score every completed run against it."""
    if not body.text.strip():
        raise api_error(422, "invalid_reference", "reference text must not be blank")
    _owned_item(session, principal.organization_id, item_id)
    testbench.collect(session, organization_id=principal.organization_id, item_id=item_id)
    with _immediate(session):
        item = _owned_item(session, principal.organization_id, item_id)
        item.reference_text = body.text
        item.reference_set_at = clock.now()
        session.add(item)
        testbench.rescore(session, item, testbench.storage_id_of(session, item.organization_id))
    session.refresh(item)
    return _item_detail(session, item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: int,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete the item with its runs and files; jobs still queued or running are cancelled."""
    storage_id = testbench.storage_id_of(session, principal.organization_id)
    with _immediate(session):
        item = _owned_item(session, principal.organization_id, item_id)
        runs = _item_runs(session, item)
        leftovers = testbench.purge_runs(session, storage_id, runs)
        leftovers.append(storage.testbench_item_path(storage_id, item.id))
        session.delete(item)
    testbench.remove_paths(leftovers)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- runs -----------------------------------------------------------------

@router.post("/items/{item_id}/runs", status_code=201)
def create_runs(
    item_id: int,
    body: RunsBody,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Queue one bench run per candidate. Each must be one an approved worker has tested; a
    candidate that already has an active run on the item is skipped."""
    created: list[int] = []
    with _immediate(session):
        item = _owned_item(session, principal.organization_id, item_id)
        offered = testbench.offered_candidates(session, principal.organization_id)
        allowed = {Candidate.model_validate(e["candidate"]) for e in offered if e["kind"] == item.kind}
        for candidate in body.candidates:
            if candidate not in allowed:
                raise api_error(422, "unavailable_candidate",
                                f"{candidate.engine}/{candidate.model} on {candidate.accelerator} is not "
                                f"available for {item.kind} items")
        active = {
            run.candidate
            for run in _item_runs(session, item)
            if run.status in testbench.ACTIVE
        }
        for candidate in body.candidates:
            key = testbench.candidate_json(candidate)
            if key in active:
                continue
            active.add(key)
            created.append(testbench.queue_run(session, item, candidate, testbench.BENCH).id)
    views = testbench.Views(session, principal.organization_id)
    runs = [session.get(TestbenchRun, run_id) for run_id in created]
    return {"items": [views.run_summary(run) for run in runs]}


@router.get("/items/{item_id}/runs/{run_id}")
def get_run(
    item_id: int,
    run_id: int,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    item = _owned_item(session, principal.organization_id, item_id)
    _owned_run(session, item, run_id)
    testbench.collect(session, organization_id=principal.organization_id, item_id=item.id)
    session.refresh(item)
    run = _owned_run(session, item, run_id)
    return testbench.Views(session, principal.organization_id).run_detail(item, run)


@router.delete("/items/{item_id}/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    item_id: int,
    run_id: int,
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> Response:
    """Delete a run and its captured result; its job is cancelled when still active."""
    storage_id = testbench.storage_id_of(session, principal.organization_id)
    with _immediate(session):
        item = _owned_item(session, principal.organization_id, item_id)
        run = _owned_run(session, item, run_id)
        leftovers = testbench.purge_runs(session, storage_id, [run])
    testbench.remove_paths(leftovers)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- leaderboard ----------------------------------------------------------

@router.get("/leaderboard")
def get_leaderboard(
    principal: Principal = Depends(curator),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Candidates ranked by mean WER over completed bench runs on corrected items (drafts excluded)."""
    testbench.collect(session, organization_id=principal.organization_id)
    return testbench.leaderboard(session, principal.organization_id)
