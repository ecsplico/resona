"""Evaluation summaries, baselines and promotion (metrics only, org-scoped, gated)."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_api import releases
from resona_api.evaluations_routes import absolute_failures, regression_failures

CORPUS = "c" * 64


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def release_id() -> str:
    from resona_api.db.engine import engine

    rid = f"9.1.0+e{uuid.uuid4().hex[:10]}"
    body = {
        "release_id": rid, "created_at": "2026-11-01T10:00:00Z",
        "wheels": [{"name": "resona-worker", "version": rid, "sha256": "b" * 64}],
        "images": [], "models": [], "worker_protocol": 1,
    }
    with Session(engine) as session:
        releases.stage_release(session, json.dumps(body).encode())
    return rid


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def message(response) -> str:
    body = response.json()
    return body.get("message") or body["detail"]["message"]


def body(release_id: str, fixtures: dict | None = None, *, passed: bool = True, platform: str = "cpu", **changes) -> dict:
    return {
        "profile": "cpu-private", "execution_profile": "cpu-private", "platform": platform,
        "release_id": release_id, "corpus_sha256": CORPUS, "baseline_revision": None,
        "fixtures": fixtures or {
            "de-short": {"wer": 0.10, "cer": 0.04},
            "silence": {"silence_insertions": 0},
            "table": {"cer": 0.01, "missing_cells": 0, "order_failures": 0},
        },
        "gate": {"passed": passed, "failures": [] if passed else ["de-short: wer"]},
        **changes,
    }


_CHECK_OF_METRIC = {
    "wer": "wer", "cer": "cer", "silence_insertions": "silence",
    "missing_cells": "table_cells", "order_failures": "reading_order",
}


def declared(payload: dict) -> dict:
    """``payload`` plus the fixture_checks its metrics imply (as the eval CLI sends them)."""
    checks = {
        fid: {
            "kind": "audio" if {"wer", "silence_insertions"} & set(metrics) else "document",
            "checks": [_CHECK_OF_METRIC[m] for m in metrics if m in _CHECK_OF_METRIC],
        }
        for fid, metrics in payload["fixtures"].items()
    }
    return {"fixture_checks": checks, **payload}


def post(http, headers, payload):
    return http.post("/v1/evaluations", json=declared(payload), headers=headers)


def record(http, headers, payload) -> int:
    response = post(http, headers, payload)
    assert response.status_code == 201, response.text
    return response.json()["evaluation_id"]


def promote(http, headers, evaluation_id, expected, **extra):
    return http.post(f"/v1/evaluations/{evaluation_id}/promote",
                     json={"expected_baseline_revision": expected, **extra}, headers=headers)


def test_create_and_get_round_trip_metrics_only(http, fresh_org, admin_headers, release_id) -> None:
    admin = admin_headers(fresh_org)
    payload = body(release_id)

    created = post(http, admin, payload)

    assert created.status_code == 201
    view = created.json()
    assert view["status"] == "recorded"
    assert {k: view[k] for k in payload} == payload
    assert http.get(f"/v1/evaluations/{view['evaluation_id']}", headers=admin).json() == view


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(transcript="Hallo Welt"),
        lambda b: b["fixtures"]["de-short"].update(hypothesis="Hallo Welt"),
        lambda b: b["fixtures"]["de-short"].update(wer="0.1"),
        lambda b: b.update(platform="windows"),
        lambda b: b.update(corpus_sha256="nope"),
        lambda b: b["gate"].update(passed="yes"),
        lambda b: b.update(fixtures={}),
    ],
)
def test_text_payloads_and_malformed_metrics_are_rejected(http, fresh_org, admin_headers, release_id, mutate) -> None:
    payload = body(release_id)
    mutate(payload)

    response = post(http, admin_headers(fresh_org), payload)

    assert response.status_code == 422
    assert "Hallo" not in response.text


def test_unknown_release_is_refused(http, fresh_org, admin_headers) -> None:
    response = post(http, admin_headers(fresh_org), body("9.9.9+nope"))
    assert (response.status_code, error_code(response)) == (422, "release_not_staged")


def test_client_tokens_and_other_organizations_are_kept_out(
    http, fresh_org, admin_headers, client_headers, release_id
) -> None:
    from resona_api.admin import create_organization
    from resona_api.db.engine import engine

    evaluation_id = record(http, admin_headers(fresh_org), body(release_id))
    other = f"org-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session:
        create_organization(session, other)

    client = client_headers(fresh_org)
    assert post(http, client, body(release_id)).status_code == 403
    assert http.get(f"/v1/evaluations/{evaluation_id}", headers=client).status_code == 403
    assert promote(http, client, evaluation_id, None).status_code == 403
    outsider = admin_headers(other)
    assert http.get(f"/v1/evaluations/{evaluation_id}", headers=outsider).status_code == 404
    assert promote(http, outsider, evaluation_id, None).status_code == 404


def test_missing_baseline_is_baseline_required(http, fresh_org, admin_headers) -> None:
    response = http.get("/v1/baselines?execution_profile=cpu-private&platform=cpu", headers=admin_headers(fresh_org))
    assert (response.status_code, error_code(response)) == (404, "baseline_required")


def test_first_promotion_creates_revision_one_and_baseline_is_readable(
    http, fresh_org, admin_headers, release_id
) -> None:
    admin = admin_headers(fresh_org)
    evaluation_id = record(http, admin, body(release_id))

    promoted = promote(http, admin, evaluation_id, None)

    assert promoted.status_code == 200
    assert promoted.json()["baseline_revision"] == 1
    baseline = http.get("/v1/baselines?execution_profile=cpu-private&platform=cpu", headers=admin).json()
    assert baseline["revision"] == 1
    assert baseline["evaluation_id"] == evaluation_id
    assert baseline["release_id"] == release_id
    assert baseline["corpus_sha256"] == CORPUS
    assert baseline["fixtures"]["de-short"] == {"wer": 0.10, "cer": 0.04}
    assert http.get(f"/v1/evaluations/{evaluation_id}", headers=admin).json()["status"] == "promoted"
    # Baselines are separate per platform.
    other_platform = http.get("/v1/baselines?execution_profile=cpu-private&platform=mac-metal", headers=admin)
    assert other_platform.status_code == 404
    again = promote(http, admin, evaluation_id, 1)
    assert again.status_code == 409


def test_failed_gate_is_never_promoted(http, fresh_org, admin_headers, release_id) -> None:
    admin = admin_headers(fresh_org)
    response = promote(http, admin, record(http, admin, body(release_id, passed=False)), None)
    assert (response.status_code, error_code(response)) == (422, "gate_failed")


@pytest.mark.parametrize(
    "fixtures",
    [
        {"table": {"cer": 0.01, "missing_cells": 1, "order_failures": 0}},
        {"table": {"cer": 0.01, "missing_cells": 0, "order_failures": 2}},
        {"silence": {"silence_insertions": 1}},
    ],
)
def test_first_baseline_needs_clean_absolute_checks_even_if_the_client_says_passed(
    http, fresh_org, admin_headers, release_id, fixtures
) -> None:
    admin = admin_headers(fresh_org)
    response = promote(http, admin, record(http, admin, body(release_id, fixtures)), None)

    assert (response.status_code, error_code(response)) == (422, "gate_failed")
    assert http.get("/v1/baselines?execution_profile=cpu-private&platform=cpu", headers=admin).status_code == 404


def test_later_promotion_checks_revision_and_regressions(http, fresh_org, admin_headers, release_id) -> None:
    admin = admin_headers(fresh_org)
    assert promote(http, admin, record(http, admin, body(release_id)), None).status_code == 200

    stale = promote(http, admin, record(http, admin, body(release_id)), None)
    assert (stale.status_code, error_code(stale)) == (409, "baseline_revision_mismatch")

    worse = body(release_id)
    worse["fixtures"]["de-short"]["wer"] = 0.1201
    response = promote(http, admin, record(http, admin, worse), 1)
    assert (response.status_code, error_code(response)) == (422, "gate_failed")
    assert "de-short: wer" in message(response)

    dropped = body(release_id)
    del dropped["fixtures"]["table"]
    response = promote(http, admin, record(http, admin, dropped), 1)
    assert (response.status_code, error_code(response)) == (422, "gate_failed")
    assert "baseline_required" in message(response)

    within = body(release_id)
    within["fixtures"]["de-short"]["wer"] = 0.12
    within["fixtures"]["de-short"]["cer"] = 0.01
    ok = promote(http, admin, record(http, admin, within), 1)
    assert ok.status_code == 200
    assert ok.json()["baseline_revision"] == 2


@pytest.mark.parametrize(
    "fixture_checks",
    [
        # A declared table check whose metric is absent.
        {"table": {"kind": "document", "checks": ["cer", "table_cells", "reading_order"]}},
        # An audio fixture declaring a document-only check.
        {"de-short": {"kind": "audio", "checks": ["wer", "cer", "table_cells"]}},
        # A metric nobody declared.
        {"de-short": {"kind": "audio", "checks": ["cer"]}},
        # A fixture without declared checks.
        {"de-short": None},
    ],
)
def test_metrics_must_be_exactly_what_declared_checks_produce(
    http, fresh_org, admin_headers, release_id, fixture_checks
) -> None:
    payload = declared(body(release_id, {
        "de-short": {"wer": 0.1, "cer": 0.04},
        "table": {"cer": 0.01, "order_failures": 0},
    }))
    payload["fixture_checks"]["table"] = {"kind": "document", "checks": ["cer", "reading_order"]}
    for fid, checks in fixture_checks.items():
        if checks is None:
            del payload["fixture_checks"][fid]
        else:
            payload["fixture_checks"][fid] = checks

    response = http.post("/v1/evaluations", json=payload, headers=admin_headers(fresh_org))

    assert (response.status_code, error_code(response)) == (422, "incomplete_metrics")


def test_empty_metrics_cannot_pass_the_absolute_checks(http, fresh_org, admin_headers, release_id) -> None:
    payload = declared(body(release_id))
    payload["fixtures"]["table"] = {}

    response = http.post("/v1/evaluations", json=payload, headers=admin_headers(fresh_org))

    assert (response.status_code, error_code(response)) == (422, "incomplete_metrics")
    assert "table: missing_cells missing" in message(response)


def test_changed_corpus_needs_explicit_rebaseline(http, fresh_org, admin_headers, release_id) -> None:
    admin = admin_headers(fresh_org)
    assert promote(http, admin, record(http, admin, body(release_id)), None).status_code == 200
    # Far worse than the baseline, but measured on another corpus: no comparison possible.
    other = body(release_id, corpus_sha256="d" * 64)
    other["fixtures"]["de-short"]["wer"] = 0.5
    evaluation_id = record(http, admin, other)

    refused = promote(http, admin, evaluation_id, 1)
    assert (refused.status_code, error_code(refused)) == (409, "baseline_corpus_mismatch")
    assert http.get("/v1/baselines?execution_profile=cpu-private&platform=cpu", headers=admin).json()["revision"] == 1

    rebased = promote(http, admin, evaluation_id, 1, rebaseline=True)
    assert rebased.status_code == 200, rebased.text
    baseline = http.get("/v1/baselines?execution_profile=cpu-private&platform=cpu", headers=admin).json()
    assert (baseline["revision"], baseline["corpus_sha256"]) == (2, "d" * 64)


def test_rebaseline_still_needs_clean_absolute_checks(http, fresh_org, admin_headers, release_id) -> None:
    admin = admin_headers(fresh_org)
    assert promote(http, admin, record(http, admin, body(release_id)), None).status_code == 200
    broken = body(release_id, corpus_sha256="d" * 64)
    broken["fixtures"]["table"]["missing_cells"] = 1

    response = promote(http, admin, record(http, admin, broken), 1, rebaseline=True)

    assert (response.status_code, error_code(response)) == (422, "gate_failed")


def test_pure_gate_helpers() -> None:
    assert absolute_failures({"a": {"missing_cells": 0}, "b": {"silence_insertions": 2}}) == [
        "b: silence_insertions=2"
    ]
    baseline = {"a": {"wer": 0.1}, "b": {"cer": 0.2}}
    assert regression_failures({"a": {"wer": 0.12}, "b": {"cer": 0.2}}, baseline) == []
    failures = regression_failures({"a": {"wer": 0.13}}, baseline)
    assert failures[0].startswith("a: wer 0.1300")
    assert failures[1] == "b: baseline_required (fixture missing from evaluation)"
    # Absolute metrics of the baseline must be present too.
    assert regression_failures({"t": {"cer": 0.1}}, {"t": {"cer": 0.1, "missing_cells": 0}}) == [
        "t: missing_cells missing"
    ]
