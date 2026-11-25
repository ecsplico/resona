"""Admission of hub processing chains under the organization's execution policy.

Every hub path that processes content (queued jobs, synchronous audio, live
streams) resolves its complete chain here before any engine or provider is
called: the ASR stage plus every postprocess stage. A denied stage raises
:class:`PolicyDenied` (``code == "policy_denied"``); nothing is silently
substituted and no provider is contacted.

Postprocess profiles are organization-owned and referenced by name. Hosted
profiles never reference local paths: a replacements ``source`` is rejected
because it would read a file from the hub host. ``include`` steps resolve at
admission against the organization's stored profiles, then the bundled blocks
(:func:`resolve_includes`), so every pinned profile document is flat.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlmodel import Session, select

from resona_job_core import (
    Endpoint,
    EndpointKind,
    ExecutionPolicy,
    OrganizationSettings,
    PolicyDenied,
    Stage,
    check_policy,
    cloud_asr_endpoint_id,
)
from resona_postprocess.compose import bundled_block, flatten_with_includes
from resona_postprocess.pipeline import PostprocessPipeline, build_pipeline
from resona_postprocess.profile import Profile, ProfileError, bundled_default
from resona_postprocess.roles import resolve_roles

from . import engine_registry as reg
from .db.models import ProfileRevision
from .settings_store import current_settings

PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-_]{0,63}$")
BUNDLED_PROFILE_NAME = "default"


class ProfileNotFound(LookupError):
    """No organization profile (and not the bundled default) has this name."""


def is_profile_name(value: str) -> bool:
    """True when ``value`` is a syntactically valid hosted profile name."""
    return bool(PROFILE_NAME_RE.fullmatch(value))


def validate_profile_body(data: dict) -> Profile:
    """Parse a hosted profile body; reject steps that read hub-local files."""
    profile = Profile.from_dict(data)
    for idx, step in enumerate(profile.steps):
        if "source" in step:
            raise ProfileError(
                f"Step {idx}: 'source' is not allowed in hosted profiles; use inline 'rules'"
            )
    return profile


def _latest_row(session: Session, organization_id: str, name: str) -> ProfileRevision | None:
    return session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id)
        .where(ProfileRevision.name == name)
        .order_by(ProfileRevision.revision.desc())
    ).first()


def load_org_profile(session: Session, organization_id: str, name: str) -> Profile:
    """Load the latest non-deleted revision of a named profile for an organization, as stored.

    The bundled ``default`` profile is readable by every organization when the
    organization has no revision of that name. Include steps stay as they are;
    :func:`load_resolved_profile` flattens them. Raises :class:`ProfileNotFound`
    for unknown names and :class:`ProfileError` for a stored body that no longer
    validates.
    """
    if not is_profile_name(name):
        raise ProfileNotFound(name)
    row = _latest_row(session, organization_id, name)
    if row is None or row.deleted:
        if name == BUNDLED_PROFILE_NAME:
            return bundled_default(flat=False)
        raise ProfileNotFound(name)
    return validate_profile_body(json.loads(row.profile_json))


def live_profiles(session: Session, organization_id: str) -> dict[str, ProfileRevision]:
    """The latest revision of every stored, non-deleted profile of an organization, by name."""
    rows = session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id)
        .order_by(ProfileRevision.name, ProfileRevision.revision)
    ).all()
    latest: dict[str, ProfileRevision] = {}
    for row in rows:
        latest[row.name] = row
    return {name: row for name, row in latest.items() if not row.deleted}


def included_names(profile: Profile) -> list[str]:
    """The profiles ``profile`` includes directly, in step order."""
    return [step["profile"] for step in profile.steps if step.get("type") == "include"]


def resolve_includes(
    session: Session,
    organization_id: str,
    profile: Profile,
    *,
    overrides: dict[str, Profile] | None = None,
) -> tuple[Profile, list[dict]]:
    """Flatten ``profile`` against the organization's profiles; name what it included.

    An include names an organization profile (latest live revision), else a bundled
    block (revision 0). ``overrides`` replace stored profiles by name, so a write can
    be checked before it is stored. Returns the flat profile and ``[{name, revision}]``
    in first-use order. Raises :class:`ProfileError` for a missing include, a cycle,
    nesting too deep, an included profile with instructions or context, or a stored
    profile that no longer validates.
    """
    overrides = overrides or {}
    revisions: dict[str, int] = {}

    def lookup(name: str) -> Profile | None:
        if name in overrides:
            row = _latest_row(session, organization_id, name)
            revisions[name] = (row.revision if row is not None else 0) + 1
            return overrides[name]
        if is_profile_name(name):
            row = _latest_row(session, organization_id, name)
            if row is not None and not row.deleted:
                revisions[name] = row.revision
                try:
                    return validate_profile_body(json.loads(row.profile_json))
                except ProfileError as exc:
                    raise ProfileError(f"included profile {name!r} is invalid: {exc}") from exc
        block = bundled_block(name)
        if block is not None:
            revisions[name] = 0
        return block

    flat, names = flatten_with_includes(profile, lookup)
    return flat, [{"name": name, "revision": revisions[name]} for name in names]


def load_resolved_profile(session: Session, organization_id: str, name: str) -> tuple[Profile, list[dict]]:
    """:func:`load_org_profile`, flattened by :func:`resolve_includes`."""
    return resolve_includes(session, organization_id, load_org_profile(session, organization_id, name))


def dependents(session: Session, organization_id: str, name: str) -> list[str]:
    """Stored profiles that include ``name``, directly or through other stored profiles, sorted."""
    graph: dict[str, list[str]] = {}
    for other, row in live_profiles(session, organization_id).items():
        try:
            graph[other] = included_names(Profile.from_dict(json.loads(row.profile_json)))
        except (ProfileError, ValueError):
            graph[other] = []
    found: set[str] = set()
    frontier = [name]
    while frontier:
        target = frontier.pop()
        for other, includes in graph.items():
            if target in includes and other not in found and other != name:
                found.add(other)
                frontier.append(other)
    return sorted(found)


def cloud_endpoint_allowed(settings: OrganizationSettings, engine: str) -> bool:
    """Whether the org's cloud policy approves ``engine``'s cloud ASR endpoint.

    Engines without a cloud ASR endpoint (local engines) are always allowed.
    """
    endpoint_id = cloud_asr_endpoint_id(engine)
    if endpoint_id is None:
        return True
    cloud_policy = settings.policy(private=False)
    endpoint = cloud_policy.endpoint(endpoint_id)
    if endpoint is None:
        return False
    try:
        check_policy(cloud_policy, [Stage(name="asr", kind="asr", endpoint=endpoint)])
    except PolicyDenied:
        return False
    return True


def asr_stage(info: reg.EngineInfo, settings: OrganizationSettings) -> Stage:
    """The ASR stage for a resolved engine.

    Local engines run inside the hub's trust boundary and are modelled as
    in-process (``endpoint=None``). A cloud provider must be a registered
    ``cloud-asr:<provider>`` endpoint; an unregistered one stays unapproved so
    the policy denies it.
    """
    if info.kind == "local":
        return Stage(name="asr", kind="asr", endpoint=None)
    endpoint_id = f"cloud-asr:{info.provider}"
    registered = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
    endpoint = registered or Endpoint(
        id=endpoint_id,
        kind=EndpointKind.CLOUD_ASR,
        model=reg.CLOUD_STT_MODELS.get(info.provider or "", info.provider or ""),
    )
    return Stage(name="asr", kind="asr", endpoint=endpoint)


@dataclass(frozen=True)
class Admission:
    """An admitted processing chain: everything resolved before any content is touched."""

    settings_revision: int
    policy: ExecutionPolicy
    engine: reg.EngineInfo
    language: str
    profile_name: str
    profile: Profile
    pipeline: PostprocessPipeline


def admit(
    session: Session,
    organization_id: str,
    *,
    private: bool,
    engine: str | None = None,
    profile: str | None = None,
    language: str | None = None,
    live: bool = False,
) -> Admission:
    """Resolve and policy-check the whole chain for one request.

    Raises ``PolicyDenied`` when any stage is not allowed, ``LookupError`` when
    the organization has no settings or the profile name is unknown,
    ``ProfileError`` for an invalid stored profile or an unassigned model role
    (``RoleUnassigned``, message ``role_unassigned: …``), and registry errors for
    engine selection problems. Callers must map these before processing starts.
    ``live``: a live session (``WS /v1/listen``); without ``engine`` the organization's
    ``live.engine`` is preferred over ``RESONA_DEFAULT_ENGINE``.
    """
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError as exc:
        raise PolicyDenied("settings", "organization has no settings revision") from exc
    if not private and not settings.cloud_enabled:
        raise PolicyDenied("policy", "cloud processing is disabled for this organization")
    policy = settings.policy(private=private)
    try:
        preferred = settings.live.engine if live and settings.live else None
        info = reg.resolve(engine, "stt", private=private, preferred=preferred)
    except reg.PrivacyViolationError as exc:
        raise PolicyDenied("asr", str(exc)) from exc
    check_policy(policy, [asr_stage(info, settings)])

    profile_name = profile or settings.postprocess_profile or BUNDLED_PROFILE_NAME
    prof, _ = load_resolved_profile(session, organization_id, profile_name)
    prof = resolve_roles(prof, settings)  # RoleUnassigned is a ProfileError (ValueError)
    pipeline = build_pipeline(prof, policy=policy)
    return Admission(
        settings_revision=revision,
        policy=policy,
        engine=info,
        language=language or settings.language,
        profile_name=profile_name,
        profile=prof,
        pipeline=pipeline,
    )


def admit_speech(session: Session, organization_id: str, *, private: bool,
                 engine: str | None) -> reg.EngineInfo:
    """Resolve a TTS engine. Cloud TTS is egress: allowed only when not private and enabled."""
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError as exc:
        raise PolicyDenied("settings", "organization has no settings revision") from exc
    info = reg.resolve(engine, "tts", private=private)
    if info.kind == "cloud":
        if private:
            raise PolicyDenied("tts", "private request cannot use a cloud engine")
        if not settings.cloud_enabled:
            raise PolicyDenied("tts", "cloud processing is disabled for this organization")
    return info
