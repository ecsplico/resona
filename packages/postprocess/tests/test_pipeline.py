import pytest

from resona_postprocess.pipeline import PostprocessPipeline


def test_empty_pipeline_is_noop():
    p = PostprocessPipeline()
    assert p.run("hello") == "hello"


def test_single_step():
    p = PostprocessPipeline()
    p.add("upper", str.upper)
    assert p.run("hello") == "HELLO"


def test_chained_steps_run_in_order():
    p = PostprocessPipeline()
    p.add("prefix", lambda t: f"[{t}]")
    p.add("upper", str.upper)
    assert p.run("hello") == "[HELLO]"


def test_add_returns_self():
    p = PostprocessPipeline()
    result = p.add("noop", lambda t: t)
    assert result is p


def test_fluent_api():
    result = (
        PostprocessPipeline()
        .add("exclaim", lambda t: t + "!")
        .add("upper", str.upper)
        .run("hello")
    )
    assert result == "HELLO!"


def test_pipeline_step_exception_propagates():
    """A step that raises ValueError must not be swallowed by the pipeline."""
    def boom(text: str) -> str:
        raise ValueError("step failed")

    p = PostprocessPipeline()
    p.add("boom", boom)
    with pytest.raises(ValueError, match="step failed"):
        p.run("hello")


def test_pipeline_step_exception_preserves_context():
    """The original exception must be accessible (not wrapped in a new one)."""
    original = ValueError("original error")

    def reraise(text: str) -> str:
        raise original

    p = PostprocessPipeline()
    p.add("reraise", reraise)
    with pytest.raises(ValueError) as exc_info:
        p.run("hello")
    assert exc_info.value is original


def test_pipeline_with_none_return_from_step():
    """A step returning None causes a TypeError when the next step tries to use it."""
    p = PostprocessPipeline()
    p.add("returns_none", lambda t: None)
    p.add("upper", str.upper)
    with pytest.raises(TypeError):
        p.run("hello")


def test_mixed_replacements_and_callable_steps():
    """Pipeline with replacements step followed by a custom callable."""
    from resona_postprocess.replacements import apply_replacements

    rules = [{"name": "hello", "replacement": "hi"}]
    p = PostprocessPipeline()
    p.add("replacements", lambda t, r=rules: apply_replacements(t, r))
    p.add("upper", str.upper)

    result = p.run("hello world")
    assert result == "HI WORLD"


def test_pipeline_preserves_unicode():
    """Pipeline correctly handles unicode text (medical German, accents, etc)."""
    p = PostprocessPipeline()
    p.add("noop", lambda t: t)
    assert p.run("Ärztlicher Befund: Müdigkeit, Übelkeit") == "Ärztlicher Befund: Müdigkeit, Übelkeit"
