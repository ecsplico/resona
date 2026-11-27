"""Model roles: ``"endpoint": "@<role>"`` in an llm/extract step.

An organization maps role names to endpoint ids (``OrganizationSettings.model_roles``).
:func:`resolve_roles` runs at admission, after include flattening and before
``build_pipeline``, so the pinned profile names a concrete endpoint and the policy
check, the worker's endpoint assignment and the worker itself see no role.
"""

from __future__ import annotations

from dataclasses import replace

from resona_job_core import OrganizationSettings

from .profile import LLM_STEP_TYPES, Profile, ProfileError

ROLE_PREFIX = "@"


class RoleUnassigned(ProfileError):
    """A step names a model role the organization has not assigned to an endpoint."""

    def __init__(self, role: str) -> None:
        super().__init__(f"role_unassigned: model role {role!r} is not assigned to an endpoint")
        self.role = role


def step_role(step: dict) -> str | None:
    """The role an llm/extract step names with ``"endpoint": "@<role>"``, else None."""
    endpoint = step.get("endpoint")
    if step.get("type") in LLM_STEP_TYPES and isinstance(endpoint, str) and endpoint.startswith(ROLE_PREFIX):
        return endpoint[len(ROLE_PREFIX):]
    return None


def resolve_roles(profile: Profile, settings: OrganizationSettings) -> Profile:
    """``profile`` with every ``@<role>`` endpoint replaced by the role's endpoint id.

    Raises :class:`RoleUnassigned` for a role missing from ``settings.model_roles``.
    Returns ``profile`` itself when no step names a role.
    """
    if not any(step_role(step) is not None for step in profile.steps):
        return profile
    steps: list[dict] = []
    for step in profile.steps:
        role = step_role(step)
        if role is None:
            steps.append(step)
            continue
        endpoint_id = settings.model_roles.get(role)
        if endpoint_id is None:
            raise RoleUnassigned(role)
        steps.append({**step, "endpoint": endpoint_id})
    return replace(profile, steps=steps)
