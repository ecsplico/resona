from resona_postprocess.replacements import apply_replacements


def test_simple_replacement():
    text = "hello world"
    rules = [{"name": "hello", "replacement": "goodbye"}]
    assert apply_replacements(text, rules) == "goodbye world"


def test_case_insensitive():
    text = "Hello World"
    rules = [{"name": "hello", "replacement": "goodbye"}]
    assert apply_replacements(text, rules) == "goodbye World"


def test_regex_pattern():
    text = "Dr. Smith arrived"
    rules = [{"name": r"\bDr\.", "replacement": "Doctor"}]
    assert apply_replacements(text, rules) == "Doctor Smith arrived"


def test_multiple_replacements_in_order():
    text = "foo bar baz"
    rules = [
        {"name": "foo", "replacement": "AAA"},
        {"name": "bar", "replacement": "BBB"},
    ]
    assert apply_replacements(text, rules) == "AAA BBB baz"


def test_invalid_regex_skipped():
    text = "hello world"
    rules = [{"name": "[invalid", "replacement": "x"}]
    result = apply_replacements(text, rules)
    assert result == "hello world"


def test_empty_replacements():
    assert apply_replacements("hello", []) == "hello"
