"""Profile — a named postprocessing configuration bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

VALID_STEP_TYPES = {"replacements", "llm", "extract"}


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
    elif stype == "extract":
        if not step.get("prompt"):
            raise ProfileError(f"Step {idx}: extract step needs a 'prompt'")
        name = step.get("name", "extract")
        if name in extract_names:
            raise ProfileError(f"Step {idx}: duplicate extract name {name!r}")
        extract_names.add(name)


@dataclass
class Profile:
    """A named postprocessing configuration: initial prompt + ordered steps."""

    name: str
    description: str = ""
    initial_prompt: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    base_dir: Path | None = None  # for resolving relative `source` paths

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
        return cls(
            name=name,
            description=data.get("description", ""),
            initial_prompt=[str(p) for p in ip],
            steps=steps,
            base_dir=base_dir,
        )

    @classmethod
    def from_file(cls, path: Path | str) -> "Profile":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProfileError(f"Cannot load profile {path}: {e}") from e
        return cls.from_dict(data, base_dir=path.parent)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "initial_prompt": self.initial_prompt,
            "steps": self.steps,
        }

    def initial_prompt_string(self) -> str:
        """Return initial-prompt phrases joined for the engine."""
        return ", ".join(self.initial_prompt)
