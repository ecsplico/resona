"""Test helper: approve a worker release for production the way promotion does.

Production jobs run only on releases with an :class:`ApprovedReleaseTuple`, which
the hub creates when an admin promotes a measured evaluation. Route and broker
tests that are not about evaluation use :func:`approve_release` to record such a
promoted evaluation directly, instead of driving the evaluation API every time.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from sqlmodel import Session

from resona_api import clock
from resona_api.db.engine import engine
from resona_api.db.models import ApprovedReleaseTuple, Evaluation, Release
from resona_api.releases import PLATFORMS, hub_release_id, record_bundled_release
from resona_api.settings_store import current_settings


def approve_release(
    organization_id: str,
    *,
    release_id: str | None = None,
    execution_profiles: Iterable[str] | None = None,
    platforms: Iterable[str] = PLATFORMS,
) -> str:
    """Record a promoted evaluation approving ``release_id`` (default: the hub's release)
    for every given execution profile (default: all of the organization's) and platform.
    Returns the release ID."""
    now = clock.now()
    with Session(engine) as session:
        if release_id is None:
            release_id = hub_release_id()
            record_bundled_release(session)
        elif session.get(Release, release_id) is None:
            raise ValueError(f"release {release_id} is not staged")
        if execution_profiles is None:
            _, settings = current_settings(session, organization_id)
            execution_profiles = settings.execution_profiles
        for profile in execution_profiles:
            for platform in platforms:
                evaluation = Evaluation(
                    organization_id=organization_id,
                    profile=profile,
                    execution_profile=profile,
                    platform=platform,
                    release_id=release_id,
                    corpus_sha256="0" * 64,
                    fixtures_json=json.dumps({"fixture": {"cer": 0.0}}),
                    gate_json=json.dumps({"passed": True, "failures": []}),
                    promoted_revision=1,
                    created_at=now,
                )
                session.add(evaluation)
                session.flush()
                session.merge(ApprovedReleaseTuple(
                    organization_id=organization_id,
                    execution_profile=profile,
                    platform=platform,
                    release_id=release_id,
                    evaluation_id=evaluation.id,
                    approved_at=now,
                ))
        session.commit()
    return release_id
