"""Model roles: ``"endpoint": "@<role>"`` resolves to the organization's endpoint id."""

import pytest

from resona_job_core import OrganizationSettings
from resona_job_core.policy import PolicyDenied
from resona_postprocess.compose import flatten
from resona_postprocess.pipeline import build_pipeline, endpoint_ids
from resona_postprocess.profile import Profile, ProfileError
from resona_postprocess.roles import RoleUnassigned, resolve_roles, step_role

LOCAL_LLM = {"id": "local-llm", "kind": "local_llm", "model": "openai/qwen", "url": "http://127.0.0.1:9292/v1"}
OTHER_LLM = {"id": "big-llm", "kind": "local_llm", "model": "openai/big", "url": "http://127.0.0.1:9293/v1"}


def _settings(**roles: str) -> OrganizationSettings:
    return OrganizationSettings(endpoints=[LOCAL_LLM, OTHER_LLM], model_roles=roles)


def _profile() -> Profile:
    return Profile.from_dict({"name": "cleanup", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
        {"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "@polish"},
        {"type": "extract", "name": "title", "prompt": "Title as JSON.", "endpoint": "@title"},
        {"type": "llm", "name": "fixed", "prompt": "Fixed.", "endpoint": "local-llm"},
    ]})


def test_roles_resolve_to_endpoint_ids_in_place():
    resolved = resolve_roles(_profile(), _settings(polish="big-llm", title="local-llm"))
    assert [s.get("endpoint") for s in resolved.steps] == [None, "big-llm", "local-llm", "local-llm"]
    assert resolved.steps[0] == _profile().steps[0]
    assert endpoint_ids(resolved) == ["big-llm", "local-llm"]


def test_resolved_profile_passes_the_policy_check():
    settings = _settings(polish="big-llm", title="local-llm")
    build_pipeline(resolve_roles(_profile(), settings), policy=settings.policy(private=True))


def test_unresolved_role_is_denied_by_the_policy():
    """A role that skipped resolution never reaches an endpoint: the registry has no '@polish'."""
    settings = _settings(polish="big-llm", title="local-llm")
    with pytest.raises(PolicyDenied, match="@polish"):
        build_pipeline(_profile(), policy=settings.policy(private=True))


def test_unassigned_role_raises_role_unassigned():
    with pytest.raises(RoleUnassigned, match="role_unassigned") as exc:
        resolve_roles(_profile(), _settings(polish="big-llm"))
    assert exc.value.role == "title"
    assert isinstance(exc.value, ProfileError)


def test_profile_without_roles_is_returned_unchanged():
    profile = Profile.from_dict({"name": "p", "steps": [
        {"type": "llm", "name": "x", "prompt": "X.", "endpoint": "local-llm"},
    ]})
    assert resolve_roles(profile, _settings()) is profile


def test_role_prefix_only_counts_on_llm_and_extract_steps():
    assert step_role({"type": "llm", "endpoint": "@cleanup"}) == "cleanup"
    assert step_role({"type": "extract", "endpoint": "@title"}) == "title"
    assert step_role({"type": "llm", "endpoint": "local-llm"}) is None
    assert step_role({"type": "replacements", "endpoint": "@x"}) is None


def test_roles_of_included_blocks_resolve_after_flattening():
    block = Profile.from_dict({"name": "polish-block", "block": True, "steps": [
        {"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "@polish"},
    ]})
    outer = Profile.from_dict({"name": "outer", "steps": [{"type": "include", "profile": "polish-block"}]})
    flat = flatten(outer, {"polish-block": block}.get)
    resolved = resolve_roles(flat, _settings(polish="local-llm"))
    assert resolved.steps == [{"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "local-llm"}]
