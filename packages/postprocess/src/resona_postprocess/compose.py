"""Profile composition: ``include`` steps and the bundled blocks.

A *block* is a profile meant to be included by others (``"block": true`` marks it for
listings only). ``{"type": "include", "profile": "<name>"}`` puts the steps of the named
profile at that position. :func:`flatten` resolves every include, so a pipeline only
ever sees plain replacements/llm/extract steps.
"""

from __future__ import annotations

import json
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Callable

from .profile import INCLUDE_NAME_RE, Profile, ProfileError, _validate_step

MAX_INCLUDE_DEPTH = 4

Lookup = Callable[[str], "Profile | None"]


def flatten(profile: Profile, lookup: Lookup) -> Profile:
    """``profile`` with every include step replaced by the included profile's steps."""
    return flatten_with_includes(profile, lookup)[0]


def flatten_with_includes(profile: Profile, lookup: Lookup) -> tuple[Profile, list[str]]:
    """Flatten ``profile`` and name the profiles it included, in first-use order.

    Included steps go in at the include's position, recursively (at most
    :data:`MAX_INCLUDE_DEPTH` levels). ``initial_prompt`` lists merge in order without
    duplicates: the outer profile's first, then each included profile's in step order.
    A ``replacements`` step of an included profile that reads ``source`` gets its rules
    inline, so the flat profile does not depend on the included profile's directory.
    Raises :class:`ProfileError` for a cycle, a missing include, nesting deeper than
    the limit, an included profile carrying ``instructions`` or ``context``, or
    duplicate extract step names after flattening.
    """
    prompts = list(dict.fromkeys(profile.initial_prompt))
    included: list[str] = []
    steps = _expand(profile, [profile.name], lookup, prompts, included, inline_sources=False)
    extract_names: set[str] = set()
    for idx, step in enumerate(steps):
        _validate_step(step, idx, extract_names)
    flat = replace(profile, initial_prompt=prompts, steps=steps)
    return flat, list(dict.fromkeys(included))


def _expand(
    owner: Profile,
    chain: list[str],
    lookup: Lookup,
    prompts: list[str],
    included: list[str],
    *,
    inline_sources: bool,
) -> list[dict]:
    out: list[dict] = []
    for step in owner.steps:
        if step.get("type") != "include":
            if inline_sources and step.get("type") == "replacements" and "rules" not in step:
                step = _with_inline_rules(step, owner)
            out.append(dict(step))
            continue
        name = step["profile"]
        path = " -> ".join([*chain, name])
        if name in chain:
            raise ProfileError(f"include cycle: {path}")
        if len(chain) > MAX_INCLUDE_DEPTH:
            raise ProfileError(f"includes nest deeper than {MAX_INCLUDE_DEPTH} levels: {path}")
        child = lookup(name)
        if child is None:
            raise ProfileError(f"profile {chain[-1]!r} includes {name!r}, which does not exist")
        if child.instructions is not None or child.context is not None:
            raise ProfileError(
                f"included profile {name!r} carries instructions or context; "
                "only the outer profile or the job may set them"
            )
        included.append(name)
        for phrase in child.initial_prompt:
            if phrase not in prompts:
                prompts.append(phrase)
        out.extend(_expand(child, [*chain, name], lookup, prompts, included, inline_sources=True))
    return out


def _with_inline_rules(step: dict, owner: Profile) -> dict:
    from .pipeline import _load_rules

    try:
        rules = _load_rules(step, owner.base_dir)
    except (OSError, ValueError) as e:
        raise ProfileError(f"profile {owner.name!r}: cannot read rules {step.get('source')!r}: {e}") from e
    inline = {key: value for key, value in step.items() if key != "source"}
    inline["rules"] = rules
    return inline


def bundled_block(name: str) -> Profile | None:
    """The bundled block ``name`` (``resona_postprocess/blocks/<name>.json``), unflattened."""
    if not INCLUDE_NAME_RE.fullmatch(name):
        return None
    ref = resources.files("resona_postprocess").joinpath("blocks", f"{name}.json")
    if not ref.is_file():
        return None
    try:
        data = json.loads(ref.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"Cannot load bundled block {name!r}: {e}") from e
    # base_dir=None: a 'source' resolves via the bundled-resource fallback in pipeline._load_rules.
    return Profile.from_dict(data, base_dir=None)


def list_bundled_blocks() -> list[str]:
    """Names of the bundled blocks, sorted."""
    root = resources.files("resona_postprocess").joinpath("blocks")
    if not root.is_dir():
        return []
    return sorted(child.name[: -len(".json")] for child in root.iterdir() if child.name.endswith(".json"))


def bundled_lookup(name: str) -> Profile | None:
    """Include lookup over the bundled blocks."""
    return bundled_block(name)


def directory_lookup(profiles_dir: Path | str) -> Lookup:
    """Include lookup over ``<profiles_dir>/<name>.json`` files."""
    root = Path(profiles_dir)

    def lookup(name: str) -> Profile | None:
        if not INCLUDE_NAME_RE.fullmatch(name):
            return None
        path = root / f"{name}.json"
        return Profile.from_file(path) if path.is_file() else None

    return lookup


def chain_lookups(*lookups: Lookup) -> Lookup:
    """Include lookup asking each of ``lookups`` in turn; the first hit wins."""

    def lookup(name: str) -> Profile | None:
        for each in lookups:
            found = each(name)
            if found is not None:
                return found
        return None

    return lookup
