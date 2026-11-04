"""Admission of hub processing chains under the organization's execution policy.

Every hub path that processes content (queued jobs, synchronous audio, live
streams) resolves its complete chain here before any engine or provider is
called: the ASR stage plus every postprocess stage. A denied stage raises
:class:`PolicyDenied` (``code == "policy_denied"``); nothing is silently
substituted and no provider is contacted.

Postprocess profiles are organization-owned and referenced by name. Hosted
profiles never reference local paths: a replacements ``source`` is rejected
because it would read a file from the hub host.
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
)
from resona_postprocess.pipeline import PostprocessPipeline, build_pipeline
from resona_postprocess.profile import Profile, ProfileError, bundled_default

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


def load_org_profile(session: Session, organization_id: str, name: str) -> Profile:
    """Load the latest non-deleted revision of a named profile for an organization.

    The bundled ``default`` profile is readable by every organization when the
    organization has no revision of that name. Raises :class:`ProfileNotFound`
    for unknown names and :class:`ProfileError` for a stored body that no longer
    validates.
    """
    if not is_profile_name(name):
        raise ProfileNotFound(name)
    row = session.exec(
        select(ProfileRevision)
        .where(ProfileRevision.organization_id == organization_id)
        .where(ProfileRevision.name == name)
        .order_by(ProfileRevision.revision.desc())
    ).first()
    if row is None or row.deleted:
        if name == BUNDLED_PROFILE_NAME:
            return bundled_default()
        raise ProfileNotFound(name)
    return validate_profile_body(json.loads(row.profile_json))


def cloud_endpoint_allowed(settings: OrganizationSettings, engine: str) -> bool:
    """Whether the org's cloud policy approves ``engine``'s cloud ASR endpoint.

    Engines without a cloud ASR endpoint (local engines) are always allowed.
    """
    endpoint_id = reg.cloud_asr_endpoint_id(engine)
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
) -> Admission:
    """Resolve and policy-check the whole chain for one request.

    Raises ``PolicyDenied`` when any stage is not allowed, ``LookupError`` when
    the organization has no settings or the profile name is unknown,
    ``ProfileError`` for an invalid stored profile, and registry errors for
    engine selection problems. Callers must map these before processing starts.
    """
    try:
        revision, settings = current_settings(session, organization_id)
    except LookupError as exc:
        raise PolicyDenied("settings", "organization has no settings revision") from exc
    if not private and not settings.cloud_enabled:
        raise PolicyDenied("policy", "cloud processing is disabled for this organization")
    policy = settings.policy(private=private)
    try:
        info = reg.resolve(engine, "stt", private=private)
    except reg.PrivacyViolationError as exc:
        raise PolicyDenied("asr", str(exc)) from exc
    check_policy(policy, [asr_stage(info, settings)])

    profile_name = profile or settings.postprocess_profile or BUNDLED_PROFILE_NAME
    prof = load_org_profile(session, organization_id, profile_name)
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
