"""Profile — a named postprocessing configuration bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path

from resona_job_core.options import MAX_INSTRUCTIONS_CHARS

VALID_STEP_TYPES = {"replacements", "llm", "extract"}
_LLM_TYPES = ("llm", "extract")


class ProfileError(ValueError):
    """Raised when a profile is malformed."""


def _validate_step(step: dict, idx: int, extract_names: set[str]) -> None:
    if not isinstance(step, dict):
        raise ProfileError(f"Step {idx} must be an object")
    stype = step.get("type")
    if stype not in VALID_STEP_TYPES:
        raise ProfileError(
            f"Step {idx}: unknown step type {stype!r} "
            f"(expected one of {sorted(VALID_STEP_TYPES)})"
        )
    if stype == "replacements":
        if "rules" not in step and "source" not in step:
            raise ProfileError(f"Step {idx}: replacements needs 'rules' or 'source'")
        source = step.get("source")
        if source is not None:
            from pathlib import PurePosixPath, PureWindowsPath
            s = str(source)
            if (PurePosixPath(s).is_absolute() or PureWindowsPath(s).is_absolute()
                    or ".." in PurePosixPath(s).parts or ".." in PureWindowsPath(s).parts):
                raise ProfileError(
                    f"Step {idx}: replacements 'source' must be a relative path "
                    f"without '..' segments, got {source!r}"
                )
        for rule in step.get("rules", []):
            pattern = rule.get("pattern", rule.get("name"))
            if pattern is None:
                raise ProfileError(f"Step {idx}: a rule is missing 'pattern'")
            try:
                re.compile(pattern)
            except re.error as e:
                raise ProfileError(f"Step {idx}: invalid regex {pattern!r}: {e}") from e
    elif stype == "llm":
        if not step.get("prompt"):
            raise ProfileError(f"Step {idx}: llm step needs a 'prompt'")
        _validate_chunking(step, idx)
    elif stype == "extract":
        if not step.get("prompt"):
            raise ProfileError(f"Step {idx}: extract step needs a 'prompt'")
    if stype in ("llm", "extract") and "timeout" in step and not _positive_number(step["timeout"]):
        raise ProfileError(f"Step {idx}: 'timeout' must be a positive number of seconds")
    if stype == "extract":
        name = step.get("name", "extract")
        if name in extract_names:
            raise ProfileError(f"Step {idx}: duplicate extract name {name!r}")
        extract_names.add(name)


_CHUNK_ONLY_KEYS = ("max_attempts", "max_length_deviation", "preserve_markup")


def _is_int(value: object) -> bool:
    """True for an integer; ``bool`` is an ``int`` subclass but not a count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_number(value: object) -> bool:
    return (_is_int(value) or isinstance(value, float)) and value > 0


def _validate_chunking(step: dict, idx: int) -> None:
    """``chunk_chars`` switches an llm step to chunked mode; its guard keys need it."""
    if "chunk_chars" not in step:
        extra = [key for key in _CHUNK_ONLY_KEYS if key in step]
        if extra:
            raise ProfileError(f"Step {idx}: {', '.join(extra)} require 'chunk_chars'")
        return
    chunk_chars = step["chunk_chars"]
    if not _is_int(chunk_chars) or chunk_chars < 1000:
        raise ProfileError(f"Step {idx}: 'chunk_chars' must be an integer >= 1000")
    attempts = step.get("max_attempts", 3)
    if not _is_int(attempts) or not 1 <= attempts <= 10:
        raise ProfileError(f"Step {idx}: 'max_attempts' must be an integer from 1 to 10")
    if "max_length_deviation" in step and not _positive_number(step["max_length_deviation"]):
        raise ProfileError(f"Step {idx}: 'max_length_deviation' must be a positive number")
    if not isinstance(step.get("preserve_markup", False), bool):
        raise ProfileError(f"Step {idx}: 'preserve_markup' must be true or false")


def _validate_instructions(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError("'instructions' must be a non-empty string")
    if len(value) > MAX_INSTRUCTIONS_CHARS:
        raise ProfileError(f"'instructions' must be at most {MAX_INSTRUCTIONS_CHARS} characters")
    return value.strip()


@dataclass
class Profile:
    """A named postprocessing configuration: initial prompt + ordered steps.

    ``instructions`` are user instructions: every llm/extract step sends them in the
    user message ahead of the text, while the step's ``prompt`` stays the system
    message. A job's instructions replace the profile's (:meth:`with_instructions`).
    """

    name: str
    description: str = ""
    initial_prompt: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    base_dir: Path | None = None  # for resolving relative `source` paths
    instructions: str | None = None

    @classmethod
    def from_dict(cls, data: dict, *, base_dir: Path | None = None) -> "Profile":
        if not isinstance(data, dict):
            raise ProfileError("Profile must be a JSON object")
        name = data.get("name")
        if not name or not isinstance(name, str):
            raise ProfileError("Profile 'name' is required and must be a string")
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            raise ProfileError("Profile 'steps' must be a list")
        extract_names: set[str] = set()
        for idx, step in enumerate(steps):
            _validate_step(step, idx, extract_names)
        ip = data.get("initial_prompt", [])
        if not isinstance(ip, list):
            raise ProfileError("Profile 'initial_prompt' must be a list of strings")
        profile = cls(
            name=name,
            description=data.get("description", ""),
            initial_prompt=[str(p) for p in ip],
            steps=steps,
            base_dir=base_dir,
        )
        if data.get("instructions") is not None:
            profile = profile.with_instructions(data["instructions"])
        return profile

    @classmethod
    def from_file(cls, path: Path | str) -> "Profile":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProfileError(f"Cannot load profile {path}: {e}") from e
        return cls.from_dict(data, base_dir=path.parent)

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "description": self.description,
            "initial_prompt": self.initial_prompt,
            "steps": self.steps,
        }
        if self.instructions is not None:
            data["instructions"] = self.instructions
        return data

    def has_llm_step(self) -> bool:
        return any(step.get("type") in _LLM_TYPES for step in self.steps)

    def with_instructions(self, instructions: str) -> "Profile":
        """A copy whose llm/extract steps get ``instructions``; needs at least one such step."""
        if not self.has_llm_step():
            raise ProfileError(f"profile {self.name!r} has no llm or extract step to take instructions")
        return replace(self, instructions=_validate_instructions(instructions))

    def initial_prompt_string(self) -> str:
        """Return initial-prompt phrases joined for the engine."""
        return ", ".join(self.initial_prompt)


def bundled_default() -> "Profile":
    """Return the `default` profile bundled with resona-postprocess.

    Raises ProfileError if the bundled file is missing or unparseable.
    """
    try:
        ref = resources.files("resona_postprocess").joinpath("profiles/default.json")
        data = json.loads(ref.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"Cannot load bundled default profile: {e}") from e
    # base_dir=None: a 'source' of 'default_replacements.json' resolves via the
    # bundled-resource fallback in pipeline._load_rules.
    return Profile.from_dict(data, base_dir=None)


def resolve_profile(ref: Profile | dict | str, profiles_dir: Path | str) -> "Profile":
    """Resolve a profile reference to a Profile.

    `ref` may be a Profile, a parsed dict, an inline JSON string, a filesystem
    path to a .json file, or a profile name resolved against `profiles_dir`.
    A file named `<name>.json` in `profiles_dir` shadows the bundled profile.
    """
    profiles_dir = Path(profiles_dir)
    if isinstance(ref, Profile):
        return ref
    if isinstance(ref, dict):
        return Profile.from_dict(ref)
    if not isinstance(ref, str):
        raise ProfileError(f"Cannot resolve profile from {type(ref).__name__}")

    text = ref.strip()
    if text.startswith("{"):
        try:
            return Profile.from_dict(json.loads(text))
        except json.JSONDecodeError as e:
            raise ProfileError(f"Invalid inline profile JSON: {e}") from e

    path = Path(ref)
    if path.suffix == ".json" and path.exists():
        return Profile.from_file(path)

    candidate = profiles_dir / f"{ref}.json"
    if candidate.exists():
        return Profile.from_file(candidate)
    if ref == "default":
        return bundled_default()
    raise ProfileError(f"Profile not found: {ref}")


def list_profiles(profiles_dir: Path | str) -> list[dict]:
    """List `{name, description}` for every profile file in `profiles_dir`."""
    profiles_dir = Path(profiles_dir)
    out: list[dict] = []
    if not profiles_dir.is_dir():
        return out
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            p = Profile.from_file(path)
            out.append({"name": p.name, "description": p.description})
        except ProfileError:
            continue
    return out
