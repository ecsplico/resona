"""Composable postprocessing pipeline producing text + structured data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable

from .llm import llm_extract, llm_transform
from .profile import Profile
from .replacements import apply_replacements

log = logging.getLogger(__name__)

TextStep = Callable[[str], str]
ExtractStep = Callable[[str], dict]


@dataclass
class PostprocessResult:
    """Outcome of a pipeline run."""

    text: str
    data: dict = field(default_factory=dict)


class PostprocessPipeline:
    """Ordered chain of text transforms and structured-extraction steps."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, str, Callable]] = []

    def add_text(self, name: str, step: TextStep) -> "PostprocessPipeline":
        self._steps.append((name, "text", step))
        return self

    def add_extract(self, name: str, step: ExtractStep) -> "PostprocessPipeline":
        self._steps.append((name, "extract", step))
        return self

    def run(self, text: str) -> PostprocessResult:
        """Run every step. A failing llm/extract step is logged and skipped."""
        result = PostprocessResult(text=text)
        for name, kind, step in self._steps:
            try:
                if kind == "text":
                    result.text = step(result.text)
                else:
                    result.data[name] = step(result.text)
            except Exception as e:  # noqa: BLE001
                log.warning("Postprocess step %r failed, skipping: %s", name, e)
                result.data.setdefault("_skipped", []).append(name)
        return result


def _load_rules(step: dict, base_dir: Path | None) -> list[dict]:
    """Resolve a replacements step's rules from inline `rules` or `source`."""
    if "rules" in step:
        return step["rules"]
    source = step.get("source", "")
    if base_dir is not None:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    # Fall back to a file bundled in resona-postprocess (e.g. default_replacements.json).
    ref = resources.files("resona_postprocess").joinpath(source)
    if ref.is_file():
        return json.loads(ref.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"replacements source not found: {source}")


def build_pipeline(profile: Profile) -> PostprocessPipeline:
    """Build a runnable pipeline from a Profile."""
    pipe = PostprocessPipeline()
    for idx, step in enumerate(profile.steps):
        stype = step["type"]
        name = step.get("name", stype)
        if stype == "replacements":
            rules = _load_rules(step, profile.base_dir)
            pipe.add_text(name, lambda t, r=rules: apply_replacements(t, r))
        elif stype == "llm":
            pipe.add_text(name, lambda t, s=step: llm_transform(
                t, prompt=s["prompt"], model=s.get("model"),
                api_base=s.get("api_base"), temperature=s.get("temperature"),
                max_tokens=s.get("max_tokens"),
            ))
        elif stype == "extract":
            pipe.add_extract(name, lambda t, s=step: llm_extract(
                t, prompt=s["prompt"], model=s.get("model"),
                api_base=s.get("api_base"), temperature=s.get("temperature"),
            ))
    return pipe
