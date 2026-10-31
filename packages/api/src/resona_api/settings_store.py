"""Read access to an organization's revisioned settings."""

from __future__ import annotations

from sqlmodel import Session, select

from resona_job_core import OrganizationSettings

from .db.models import SettingsRevision


def current_settings(session: Session, organization_id: str) -> tuple[int, OrganizationSettings]:
    """Return ``(revision, settings)`` for the organization's latest revision.

    Raises ``LookupError`` when the organization has no settings.
    """
    row = session.exec(
        select(SettingsRevision)
        .where(SettingsRevision.organization_id == organization_id)
        .order_by(SettingsRevision.revision.desc())
    ).first()
    if row is None:
        raise LookupError(f"no settings for organization {organization_id!r}")
    return row.revision, OrganizationSettings.model_validate_json(row.settings_json)
