import json
from pathlib import Path
from unittest.mock import patch

from resona_postprocess.sources import (
    load_replacements_from_file,
    build_pipeline_from_config,
)
from resona_postprocess.pipeline import PostprocessPipeline


def test_load_replacements_from_missing_file_returns_bundled_defaults(tmp_path):
    result = load_replacements_from_file(tmp_path / "nonexistent.json")
    assert len(result) > 0
    assert result[0]["name"] == "\\s*Komma"


def test_load_replacements_from_file(tmp_path):
    f = tmp_path / "replacements.json"
    f.write_text(json.dumps([
        {"name": "foo", "replacement": "bar"},
    ]))
    result = load_replacements_from_file(f)
    assert len(result) == 1
    assert result[0]["name"] == "foo"


def test_build_pipeline_no_config(tmp_path):
    pipeline = build_pipeline_from_config(tmp_path / "nonexistent.json")
    assert isinstance(pipeline, PostprocessPipeline)
    assert pipeline.run("hello") == "hello"


def test_build_pipeline_replacements_only(tmp_path):
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "hello", "replacement": "goodbye"}]))

    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": str(replacements)},
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    assert pipeline.run("hello world") == "goodbye world"


@patch("resona_postprocess.sources.llm_postprocess", return_value="LLM OUTPUT")
def test_build_pipeline_with_llm_step(mock_llm, tmp_path):
    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {
                "type": "llm",
                "name": "format",
                "prompt": "Format this text.",
                "model": "ollama/llama3",
            },
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    result = pipeline.run("raw text")
    assert result == "LLM OUTPUT"
    mock_llm.assert_called_once_with("raw text", prompt="Format this text.", model="ollama/llama3")


def test_build_pipeline_fallback_to_replacements_json(tmp_path):
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "foo", "replacement": "bar"}]))

    pipeline = build_pipeline_from_config(
        config_path=tmp_path / "postprocess.json",
        replacements_fallback=replacements,
    )
    assert pipeline.run("foo baz") == "bar baz"


def test_relative_source_resolved_to_config_dir(tmp_path):
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps([{"name": "a", "replacement": "b"}]))

    config = tmp_path / "postprocess.json"
    config.write_text(json.dumps({
        "steps": [
            {"type": "replacements", "source": "replacements.json"},
        ]
    }))

    pipeline = build_pipeline_from_config(config)
    assert pipeline.run("a c") == "b c"
