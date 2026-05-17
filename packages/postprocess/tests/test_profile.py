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


import json as _json
from resona_postprocess.profile import (
    resolve_profile, list_profiles, bundled_default,
)


def test_bundled_default_loads():
    p = bundled_default()
    assert p.name == "default"
    assert any(s["type"] == "replacements" for s in p.steps)


def test_resolve_profile_by_name(tmp_path):
    (tmp_path / "arzt.json").write_text(_json.dumps(
        {"name": "arzt", "steps": []}))
    p = resolve_profile("arzt", tmp_path)
    assert p.name == "arzt"


def test_resolve_profile_inline_json(tmp_path):
    p = resolve_profile('{"name": "inline", "steps": []}', tmp_path)
    assert p.name == "inline"


def test_resolve_profile_dict(tmp_path):
    p = resolve_profile({"name": "d", "steps": []}, tmp_path)
    assert p.name == "d"


def test_resolve_profile_default_falls_back_to_bundled(tmp_path):
    p = resolve_profile("default", tmp_path)
    assert p.name == "default"


def test_resolve_profile_file_shadows_bundled(tmp_path):
    (tmp_path / "default.json").write_text(_json.dumps(
        {"name": "default", "description": "user", "steps": []}))
    p = resolve_profile("default", tmp_path)
    assert p.description == "user"


def test_resolve_profile_unknown_raises(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        resolve_profile("nope", tmp_path)


def test_list_profiles(tmp_path):
    (tmp_path / "a.json").write_text(_json.dumps(
        {"name": "a", "description": "AA", "steps": []}))
    out = list_profiles(tmp_path)
    assert {"name": "a", "description": "AA"} in out


def test_from_dict_rejects_source_path_traversal():
    data = _ok_profile()
    data["steps"][0] = {"type": "replacements", "source": "../../../etc/x.json"}
    with pytest.raises(ProfileError, match="source"):
        Profile.from_dict(data)


def test_from_dict_allows_plain_source_filename():
    data = _ok_profile()
    data["steps"][0] = {"type": "replacements", "source": "default_replacements.json"}
    Profile.from_dict(data)  # must not raise
