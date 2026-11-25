"""Model roles at job admission: ``"endpoint": "@<role>"`` becomes the organization's endpoint id.

Runs after include flattening and before ``build_pipeline``, so the pinned profile
document, ``check_policy`` and ``endpoints_assigned`` only ever see concrete ids.
"""

from __future__ import annotations

from resona_job_core import OrganizationSettings
from resona_postprocess.profile import Profile
from resona_postprocess.roles import RoleUnassigned, resolve_roles

from .errors import api_error


def resolve_model_roles(profile: Profile, settings: OrganizationSettings) -> Profile:
    """``profile`` with its roles resolved; an unassigned role is 422 ``role_unassigned``."""
    try:
        return resolve_roles(profile, settings)
    except RoleUnassigned as exc:
        raise api_error(
            422, "role_unassigned",
            f"model role {exc.role!r} is not assigned to an endpoint in the organization settings",
        ) from None
