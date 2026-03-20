"""Tests for ws_engine.replacements.apply_replacements (pure, stateless)."""
import pytest
from ws_engine.replacements import apply_replacements


def test_empty_list_returns_original():
    assert apply_replacements("Hello World", []) == "Hello World"


def test_single_replacement():
    result = apply_replacements("Hello World", [{"name": "World", "replacement": "Whisper"}])
    assert result == "Hello Whisper"


def test_case_insensitive():
    result = apply_replacements("hello world", [{"name": "HELLO", "replacement": "hi"}])
    assert result == "hi world"


def test_multiple_replacements_applied_in_order():
    rules = [
        {"name": "foo", "replacement": "bar"},
        {"name": "bar", "replacement": "baz"},
    ]
    result = apply_replacements("foo", rules)
    assert result == "baz"


def test_no_match_returns_original():
    result = apply_replacements("unchanged text", [{"name": "xyz", "replacement": "abc"}])
    assert result == "unchanged text"


def test_invalid_regex_skipped(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="ws_engine.replacements"):
        result = apply_replacements("hello", [{"name": "[invalid", "replacement": "x"}])
    assert result == "hello"
    assert "Invalid replacement pattern" in caplog.text


def test_empty_text():
    result = apply_replacements("", [{"name": "foo", "replacement": "bar"}])
    assert result == ""


def test_regex_pattern():
    result = apply_replacements("date: 2024-01-15", [{"name": r"\d{4}-\d{2}-\d{2}", "replacement": "DATE"}])
    assert result == "date: DATE"


def test_multiple_occurrences():
    result = apply_replacements("cat cat cat", [{"name": "cat", "replacement": "dog"}])
    assert result == "dog dog dog"


def test_empty_replacement_string():
    result = apply_replacements("remove this word", [{"name": "this ", "replacement": ""}])
    assert result == "remove word"
