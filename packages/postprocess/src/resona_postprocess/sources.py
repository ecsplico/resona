"""Load postprocessing config from files and build pipelines."""

import json
import logging
from importlib import resources
from pathlib import Path

from .llm import llm_postprocess
from .pipeline import PostprocessPipeline
from .replacements import apply_replacements

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path.home() / ".resona"


def _load_bundled_defaults() -> list[dict[str, str]]:
    """Load the default replacement rules bundled with resona-postprocess."""
    ref = resources.files("resona_postprocess").joinpath("default_replacements.json")
    return json.loads(ref.read_text(encoding="utf-8"))


def load_replacements_from_file(path: Path | None = None) -> list[dict[str, str]]:
    """Load replacement rules from a JSON file.

    Falls back to bundled defaults if no user file exists.
    """
    path = path or (_DEFAULT_CONFIG_DIR / "replacements.json")
    if not path.exists():
        return _load_bundled_defaults()
    return json.loads(path.read_text())


def build_pipeline_from_config(
    config_path: Path | None = None,
    replacements_fallback: Path | None = None,
) -> PostprocessPipeline:
    """Build a PostprocessPipeline from a config file.

    If config_path doesn't exist, falls back to a bare replacements file.
    Relative source paths in the config resolve relative to the config directory.
    """
    config_path = config_path or (_DEFAULT_CONFIG_DIR / "postprocess.json")
    config_dir = config_path.parent

    if not config_path.exists():
        fallback = replacements_fallback or (config_dir / "replacements.json")
        rules = load_replacements_from_file(fallback)
        pipeline = PostprocessPipeline()
        if rules:
            pipeline.add("replacements", lambda t, r=rules: apply_replacements(t, r))
        return pipeline

    cfg = json.loads(config_path.read_text())
    pipeline = PostprocessPipeline()

    for step in cfg.get("steps", []):
        step_type = step["type"]

        if step_type == "replacements":
            source = step.get("source")
            if source:
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = config_dir / source_path
            else:
                source_path = None
            rules = load_replacements_from_file(source_path)
            pipeline.add("replacements", lambda t, r=rules: apply_replacements(t, r))

        elif step_type == "llm":
            prompt = step["prompt"]
            model = step.get("model")
            pipeline.add(
                step.get("name", "llm"),
                lambda t, p=prompt, m=model: llm_postprocess(t, prompt=p, model=m),
            )

        else:
            log.warning(f"Unknown postprocess step type: {step_type}")

    return pipeline
