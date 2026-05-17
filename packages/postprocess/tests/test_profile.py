import pytest
from resona_postprocess.profile import Profile, ProfileError


def _ok_profile():
    return {
        "name": "p1",
        "description": "d",
        "initial_prompt": ["Befund"],
        "steps": [
            {"type": "replacements", "rules": [{"pattern": r"\bx\b", "replacement": "y"}]},
            {"type": "llm", "prompt": "format"},
            {"type": "extract", "name": "fields", "prompt": "extract"},
        ],
    }


def test_from_dict_valid():
    p = Profile.from_dict(_ok_profile())
    assert p.name == "p1"
    assert p.initial_prompt == ["Befund"]
    assert len(p.steps) == 3


def test_from_dict_requires_name():
    data = _ok_profile()
    del data["name"]
    with pytest.raises(ProfileError, match="name"):
        Profile.from_dict(data)


def test_from_dict_rejects_unknown_step_type():
    data = _ok_profile()
    data["steps"].append({"type": "magic"})
    with pytest.raises(ProfileError, match="step type"):
        Profile.from_dict(data)


def test_from_dict_rejects_uncompilable_regex():
    data = _ok_profile()
    data["steps"][0]["rules"][0]["pattern"] = "["
    with pytest.raises(ProfileError, match="regex"):
        Profile.from_dict(data)


def test_from_dict_rejects_duplicate_extract_names():
    data = _ok_profile()
    data["steps"].append({"type": "extract", "name": "fields", "prompt": "again"})
    with pytest.raises(ProfileError, match="extract"):
        Profile.from_dict(data)


def test_from_dict_rejects_llm_without_prompt():
    data = _ok_profile()
    data["steps"][1] = {"type": "llm"}
    with pytest.raises(ProfileError, match="prompt"):
        Profile.from_dict(data)


def test_to_dict_roundtrip():
    p = Profile.from_dict(_ok_profile())
    assert Profile.from_dict(p.to_dict()).steps == p.steps
