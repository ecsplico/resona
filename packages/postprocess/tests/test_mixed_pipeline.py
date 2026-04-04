"""Tests for mixed postprocessing pipelines (replacements + LLM)."""
import json
from unittest.mock import patch

from resona_postprocess.pipeline import PostprocessPipeline
from resona_postprocess.replacements import apply_replacements
from resona_postprocess.llm import llm_postprocess
from resona_postprocess.sources import build_pipeline_from_config


def test_mixed_replacements_then_llm(tmp_path):
    """Config with replacements step followed by LLM step."""
    replacements_file = tmp_path / "replacements.json"
    replacements_file.write_text(json.dumps([
        {"name": "hello", "replacement": "greetings"},
    ]))

    config_file = tmp_path / "postprocess.json"
    config_file.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": str(replacements_file)},
            {"type": "llm", "name": "format", "prompt": "Format this.", "model": "test-model"},
        ]
    }))

    with patch("resona_postprocess.sources.llm_postprocess", return_value="FORMATTED: greetings world") as mock_llm:
        pipeline = build_pipeline_from_config(config_file)
        result = pipeline.run("hello world")

    # Replacements applied first, then LLM
    assert result == "FORMATTED: greetings world"
    mock_llm.assert_called_once_with("greetings world", prompt="Format this.", model="test-model")


def test_mixed_llm_then_replacements(tmp_path):
    """LLM step first, then replacements — order matters."""
    replacements_file = tmp_path / "replacements.json"
    replacements_file.write_text(json.dumps([
        {"name": "formatted", "replacement": "FINAL"},
    ]))

    config_file = tmp_path / "postprocess.json"
    config_file.write_text(json.dumps({
        "steps": [
            {"type": "llm", "name": "preprocess", "prompt": "Clean up.", "model": "m"},
            {"type": "replacements", "source": str(replacements_file)},
        ]
    }))

    with patch("resona_postprocess.sources.llm_postprocess", return_value="formatted text"):
        pipeline = build_pipeline_from_config(config_file)
        result = pipeline.run("raw input")

    assert result == "FINAL text"


def test_multiple_replacement_steps(tmp_path):
    """Pipeline can have multiple replacement steps from different files."""
    r1 = tmp_path / "r1.json"
    r1.write_text(json.dumps([{"name": "a", "replacement": "b"}]))
    r2 = tmp_path / "r2.json"
    r2.write_text(json.dumps([{"name": "b", "replacement": "c"}]))

    config_file = tmp_path / "postprocess.json"
    config_file.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": str(r1)},
            {"type": "replacements", "source": str(r2)},
        ]
    }))

    pipeline = build_pipeline_from_config(config_file)
    result = pipeline.run("a x")
    # First step: a -> b, second step: b -> c
    assert result == "c x"
