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
