import json

import pytest
from pydantic import ValidationError

from resona_job_core import TEXT_EXECUTION_PROFILE, Candidate, OrganizationSettings, default_settings


@pytest.mark.parametrize("field", ["execution_profiles", "default_execution_profile"])
def test_text_job_execution_profile_name_is_reserved(field):
    """Text jobs record TEXT_EXECUTION_PROFILE; a real profile of that name would make them ambiguous."""
    body = default_settings().model_dump(mode="json")
    if field == "execution_profiles":
        body["execution_profiles"][TEXT_EXECUTION_PROFILE] = {"audio": [], "document": []}
    else:
        body["default_execution_profile"] = TEXT_EXECUTION_PROFILE
    with pytest.raises(ValidationError, match="reserved for text jobs"):
        OrganizationSettings.model_validate(body)


def test_profile_diarize_is_serialized_only_when_on():
    """Settings documents without the field stay readable by releases predating it (extra="forbid")."""
    settings = default_settings()
    assert all("diarize" not in p for p in settings.model_dump(mode="json")["execution_profiles"].values())
    body = settings.model_dump(mode="json")
    body["execution_profiles"]["mac-private"]["diarize"] = True
    dumped = json.loads(OrganizationSettings.model_validate(body).model_dump_json())
    assert dumped["execution_profiles"]["mac-private"]["diarize"] is True
    assert "diarize" not in dumped["execution_profiles"]["private-auto"]


def test_legacy_cloud_candidate_reads_as_external():
    """Settings and snapshots stored before accelerator 'external' hold cloud candidates on 'cpu'."""
    legacy = {"engine": "deepgram", "model": "nova-3", "accelerator": "cpu"}
    assert Candidate.model_validate(legacy).accelerator == "external"
    assert legacy["accelerator"] == "cpu"
    body = default_settings().model_dump(mode="json")
    body["execution_profiles"]["private-auto"]["audio"].append(legacy)
    loaded = OrganizationSettings.model_validate(body)
    assert loaded.execution_profiles["private-auto"].audio[-1].accelerator == "external"


def test_non_cloud_engine_on_external_is_rejected():
    with pytest.raises(ValidationError, match="accelerator 'external'"):
        Candidate(engine="faster-whisper", model="x", accelerator="external")


def _with_local_llm(body: dict) -> dict:
    body["endpoints"] = [{"id": "local-llm", "kind": "local_llm", "model": "openai/qwen", "url": "http://127.0.0.1:9292/v1"}]
    return body


def test_model_roles_map_roles_to_registered_endpoints():
    body = _with_local_llm(default_settings().model_dump(mode="json"))
    body["model_roles"] = {"polish": "local-llm", "title": "local-llm", "my-role2": "local-llm"}
    settings = OrganizationSettings.model_validate(body)
    assert settings.model_roles == {"polish": "local-llm", "title": "local-llm", "my-role2": "local-llm"}
    assert json.loads(settings.model_dump_json())["model_roles"]["polish"] == "local-llm"


@pytest.mark.parametrize("role", ["Polish", "1st", "-x", "a_b", "@polish", "", "a" * 33])
def test_model_role_name_must_match_pattern(role):
    body = _with_local_llm(default_settings().model_dump(mode="json"))
    body["model_roles"] = {role: "local-llm"}
    with pytest.raises(ValidationError, match="model role name"):
        OrganizationSettings.model_validate(body)


def test_model_role_target_must_be_a_registered_endpoint():
    body = _with_local_llm(default_settings().model_dump(mode="json"))
    body["model_roles"] = {"polish": "cloud-llm"}
    with pytest.raises(ValidationError, match="not in endpoints"):
        OrganizationSettings.model_validate(body)


def test_model_roles_are_serialized_only_when_set():
    """Settings documents without the field stay readable by releases predating it (extra="forbid")."""
    assert "model_roles" not in default_settings().model_dump(mode="json")
    assert "model_roles" not in json.loads(default_settings().model_dump_json())


def test_live_defaults_are_serialized_only_when_set():
    """Settings documents without ``live`` stay readable by releases predating it (extra="forbid")."""
    assert "live" not in default_settings().model_dump(mode="json")
    body = default_settings().model_dump(mode="json")
    body["live"] = {}
    assert "live" not in OrganizationSettings.model_validate(body).model_dump(mode="json")
    body["live"] = {"engine": "faster-whisper", "cleanup_profile": "dictation-clean-de"}
    dumped = json.loads(OrganizationSettings.model_validate(body).model_dump_json())
    assert dumped["live"] == {"engine": "faster-whisper", "cleanup_profile": "dictation-clean-de"}


def test_live_defaults_round_trip():
    body = default_settings().model_dump(mode="json")
    body["live"] = {
        "engine": "deepgram",
        "correction_execution_profile": "private-auto",
        "cleanup_profile": "dictation-clean-de",
    }
    settings = OrganizationSettings.model_validate(body)
    assert settings.live is not None
    assert settings.live.engine == "deepgram"
    assert settings.live.correction_execution_profile == "private-auto"
    assert OrganizationSettings.model_validate(settings.model_dump(mode="json")) == settings


def test_live_correction_execution_profile_must_exist():
    body = default_settings().model_dump(mode="json")
    body["live"] = {"correction_execution_profile": "nope"}
    with pytest.raises(ValidationError, match="not in execution_profiles"):
        OrganizationSettings.model_validate(body)


@pytest.mark.parametrize("live", [
    {"cleanup_profile": "Bad Name"},
    {"cleanup_profile": "../x"},
    {"engine": ""},
    {"unknown": "x"},
])
def test_live_defaults_reject_invalid_values(live):
    body = default_settings().model_dump(mode="json")
    body["live"] = live
    with pytest.raises(ValidationError):
        OrganizationSettings.model_validate(body)
