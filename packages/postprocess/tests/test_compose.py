"""Profile composition: include steps, flattening and the bundled blocks."""

import json

import pytest

from resona_job_core.policy import ExecutionPolicy
from resona_postprocess.compose import (
    MAX_INCLUDE_DEPTH,
    bundled_block,
    bundled_lookup,
    chain_lookups,
    directory_lookup,
    flatten,
    flatten_with_includes,
    list_bundled_blocks,
)
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import Profile, ProfileError, resolve_profile


def _rules(tag: str) -> dict:
    return {"type": "replacements", "name": tag, "rules": [{"pattern": tag, "replacement": tag.upper()}]}


def _extract(name: str) -> dict:
    return {"type": "extract", "name": name, "prompt": "p", "endpoint": "local-llm"}


def _lookup(*profiles: dict):
    by_name = {data["name"]: Profile.from_dict(data) for data in profiles}
    return by_name.get


def test_include_step_needs_a_profile_name() -> None:
    for bad in (None, "", "Upper", "../x", "a/b", 3):
        with pytest.raises(ProfileError, match="include step needs 'profile'"):
            Profile.from_dict({"name": "p", "steps": [{"type": "include", "profile": bad}]})
    Profile.from_dict({"name": "p", "steps": [{"type": "include", "profile": "filler-words-de"}]})


def test_block_flag_round_trips_and_must_be_bool() -> None:
    block = Profile.from_dict({"name": "b", "block": True, "steps": []})
    assert block.block is True and block.to_dict()["block"] is True
    assert "block" not in Profile.from_dict({"name": "p", "steps": []}).to_dict()
    with pytest.raises(ProfileError, match="'block'"):
        Profile.from_dict({"name": "b", "block": "yes", "steps": []})


def test_included_steps_go_in_at_the_include_position_recursively() -> None:
    lookup = _lookup(
        {"name": "a", "steps": [_rules("a1"), {"type": "include", "profile": "b"}, _rules("a2")]},
        {"name": "b", "steps": [_rules("b1")]},
        {"name": "c", "steps": [_rules("c1")]},
    )
    outer = Profile.from_dict({"name": "outer", "steps": [
        _rules("o1"), {"type": "include", "profile": "a"}, _rules("o2"), {"type": "include", "profile": "c"},
    ]})
    flat, included = flatten_with_includes(outer, lookup)
    assert [step["name"] for step in flat.steps] == ["o1", "a1", "b1", "a2", "o2", "c1"]
    assert included == ["a", "b", "c"]
    assert outer.steps[1] == {"type": "include", "profile": "a"}  # the input is not changed


def test_included_profiles_are_named_once_in_first_use_order() -> None:
    lookup = _lookup({"name": "b", "steps": [_rules("b1")]})
    outer = Profile.from_dict({"name": "o", "steps": [
        {"type": "include", "profile": "b"}, {"type": "include", "profile": "b"},
    ]})
    flat, included = flatten_with_includes(outer, lookup)
    assert [step["name"] for step in flat.steps] == ["b1", "b1"]
    assert included == ["b"]


def test_flatten_keeps_the_outer_profile_fields() -> None:
    outer = Profile.from_dict({
        "name": "o", "description": "d", "instructions": "be brief",
        "steps": [{"type": "include", "profile": "b"}, {"type": "llm", "prompt": "p", "endpoint": "x"}],
    })
    flat = flatten(outer, _lookup({"name": "b", "block": True, "steps": [_rules("b1")]}))
    assert (flat.name, flat.description, flat.instructions, flat.block) == ("o", "d", "be brief", False)


def test_initial_prompts_merge_in_order_without_duplicates() -> None:
    lookup = _lookup(
        {"name": "a", "initial_prompt": ["Befund", "Anamnese"], "steps": [{"type": "include", "profile": "b"}]},
        {"name": "b", "initial_prompt": ["Diagnose", "Befund"], "steps": []},
        {"name": "c", "initial_prompt": ["Medikation", "Diagnose"], "steps": []},
    )
    outer = Profile.from_dict({"name": "o", "initial_prompt": ["Verlauf", "Befund"], "steps": [
        {"type": "include", "profile": "a"}, {"type": "include", "profile": "c"},
    ]})
    assert flatten(outer, lookup).initial_prompt == ["Verlauf", "Befund", "Anamnese", "Diagnose", "Medikation"]


def test_nesting_up_to_the_depth_limit_works_and_deeper_fails() -> None:
    names = [f"level{i}" for i in range(1, MAX_INCLUDE_DEPTH + 2)]
    chain = [
        {"name": name, "steps": [_rules(name)] + ([{"type": "include", "profile": nxt}] if nxt else [])}
        for name, nxt in zip(names, [*names[1:], None])
    ]
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "level1"}]})

    allowed = [*chain[: MAX_INCLUDE_DEPTH - 1], {"name": names[MAX_INCLUDE_DEPTH - 1], "steps": [_rules("last")]}]
    flat = flatten(outer, _lookup(*allowed))
    assert len(flat.steps) == MAX_INCLUDE_DEPTH

    with pytest.raises(ProfileError, match=f"deeper than {MAX_INCLUDE_DEPTH} levels: o -> level1"):
        flatten(outer, _lookup(*chain))


def test_a_cycle_names_the_chain() -> None:
    lookup = _lookup(
        {"name": "a", "steps": [{"type": "include", "profile": "b"}]},
        {"name": "b", "steps": [{"type": "include", "profile": "a"}]},
    )
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "a"}]})
    with pytest.raises(ProfileError, match="include cycle: o -> a -> b -> a"):
        flatten(outer, lookup)


def test_a_profile_including_itself_is_a_cycle() -> None:
    outer = Profile.from_dict({"name": "self", "steps": [{"type": "include", "profile": "self"}]})
    with pytest.raises(ProfileError, match="include cycle: self -> self"):
        flatten(outer, _lookup())


def test_a_missing_include_is_named() -> None:
    lookup = _lookup({"name": "a", "steps": [{"type": "include", "profile": "gone"}]})
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "a"}]})
    with pytest.raises(ProfileError, match="'a' includes 'gone', which does not exist"):
        flatten(outer, lookup)


def test_an_included_profile_may_not_carry_instructions() -> None:
    lookup = _lookup({"name": "b", "instructions": "x", "steps": [{"type": "llm", "prompt": "p", "endpoint": "e"}]})
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "b"}]})
    with pytest.raises(ProfileError, match="included profile 'b' carries instructions"):
        flatten(outer, lookup)


def test_an_included_profile_may_not_carry_context() -> None:
    lookup = _lookup({"name": "b", "context": {"before": "x"}, "steps": [_rules("b1")]})
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "b"}]})
    with pytest.raises(ProfileError, match="included profile 'b' carries instructions or context"):
        flatten(outer, lookup)


def test_duplicate_extract_names_are_refused_after_flattening() -> None:
    lookup = _lookup({"name": "b", "steps": [_extract("title")]})
    outer = Profile.from_dict({"name": "o", "steps": [_extract("title"), {"type": "include", "profile": "b"}]})
    with pytest.raises(ProfileError, match="duplicate extract name 'title'"):
        flatten(outer, lookup)


def test_build_pipeline_refuses_unflattened_includes() -> None:
    profile = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "filler-words-de"}]})
    with pytest.raises(ProfileError, match="unresolved include steps \\(filler-words-de\\)"):
        build_pipeline(profile, policy=ExecutionPolicy(private=True))


def test_included_source_rules_are_inlined(tmp_path) -> None:
    (tmp_path / "rules.json").write_text(json.dumps([{"pattern": "a", "replacement": "b"}]), encoding="utf-8")
    (tmp_path / "block.json").write_text(json.dumps(
        {"name": "block", "steps": [{"type": "replacements", "name": "r", "source": "rules.json"}]}
    ), encoding="utf-8")
    outer = Profile.from_dict({"name": "o", "steps": [{"type": "include", "profile": "block"}]})
    flat = flatten(outer, directory_lookup(tmp_path))
    assert flat.steps == [{"type": "replacements", "name": "r", "rules": [{"pattern": "a", "replacement": "b"}]}]


def test_bundled_blocks_are_listed_and_marked() -> None:
    assert list_bundled_blocks() == ["dictation-commands-de", "filler-words-de", "filler-words-en"]
    for name in list_bundled_blocks():
        block = bundled_block(name)
        assert block is not None and block.name == name and block.block is True
    assert bundled_block("nope") is None
    assert bundled_block("../profiles/default") is None
    assert bundled_lookup("filler-words-en").name == "filler-words-en"


def test_chained_lookup_asks_the_profiles_dir_before_the_bundled_blocks(tmp_path) -> None:
    (tmp_path / "filler-words-de.json").write_text(
        json.dumps({"name": "filler-words-de", "steps": [_rules("mine")]}), encoding="utf-8")
    lookup = chain_lookups(directory_lookup(tmp_path), bundled_lookup)
    assert lookup("filler-words-de").steps == [_rules("mine")]
    assert lookup("filler-words-en").name == "filler-words-en"
    assert lookup("nope") is None


def test_resolve_profile_flattens_against_the_profiles_dir_then_bundled_blocks(tmp_path) -> None:
    (tmp_path / "names.json").write_text(json.dumps({"name": "names", "block": True, "steps": [
        {"type": "replacements", "rules": [{"pattern": r"\bMonique\b", "replacement": "Monic"}]},
    ]}), encoding="utf-8")
    (tmp_path / "dictation.json").write_text(json.dumps({"name": "dictation", "steps": [
        {"type": "include", "profile": "filler-words-de"},
        {"type": "include", "profile": "names"},
        {"type": "include", "profile": "dictation-commands-de"},
    ]}), encoding="utf-8")
    profile = resolve_profile("dictation", tmp_path)
    assert all(step["type"] == "replacements" for step in profile.steps)
    out = build_pipeline(profile, policy=ExecutionPolicy(private=True)).run("Monique äh Komma kommt morgen Punkt")
    assert out.text == "Monic, kommt morgen."


def test_resolve_profile_finds_a_bundled_block_by_name(tmp_path) -> None:
    profile = resolve_profile("filler-words-en", tmp_path)
    assert profile.block is True
    assert build_pipeline(profile, policy=ExecutionPolicy(private=True)).run("So, um, yes").text == "So, yes"


def test_resolve_profile_flattens_inline_and_dict_refs(tmp_path) -> None:
    data = {"name": "inline", "steps": [{"type": "include", "profile": "filler-words-de"}]}
    for ref in (data, json.dumps(data), Profile.from_dict(data)):
        assert resolve_profile(ref, tmp_path).steps == bundled_block("filler-words-de").steps
