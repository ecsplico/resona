import resona_postprocess.llm as llm_mod
from resona_postprocess.pipeline import (
    PostprocessPipeline, PostprocessResult, build_pipeline,
)
from resona_postprocess.profile import Profile


def test_pipeline_runs_text_steps_in_order():
    pipe = PostprocessPipeline()
    pipe.add_text("up", str.upper)
    pipe.add_text("excl", lambda t: t + "!")
    result = pipe.run("hi")
    assert isinstance(result, PostprocessResult)
    assert result.text == "HI!"
    assert result.data == {}


def test_pipeline_extract_step_populates_data():
    pipe = PostprocessPipeline()
    pipe.add_extract("fields", lambda t: {"len": len(t)})
    result = pipe.run("abcd")
    assert result.text == "abcd"
    assert result.data == {"fields": {"len": 4}}


def test_pipeline_failing_llm_step_is_skipped():
    pipe = PostprocessPipeline()

    def boom(_): raise RuntimeError("llm down")

    pipe.add_text("bad", boom)
    pipe.add_text("ok", str.upper)
    result = pipe.run("hi")
    assert result.text == "HI"  # bad step skipped, ok step still ran


def test_build_pipeline_replacements_and_extract(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "litellm",
        type("L", (), {"completion": staticmethod(
            lambda **k: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": '{"k": 1}'})})()],
                "usage": None})())}),
    )
    profile = Profile.from_dict({
        "name": "p",
        "steps": [
            {"type": "replacements", "rules": [{"pattern": r"\bx\b", "replacement": "y"}]},
            {"type": "extract", "name": "f", "prompt": "extract"},
        ],
    })
    result = build_pipeline(profile).run("x x")
    assert result.text == "y y"
    assert result.data == {"f": {"k": 1}}


def test_build_pipeline_replacements_from_bundled_source():
    profile = Profile.from_dict({
        "name": "p",
        "steps": [{"type": "replacements", "source": "default_replacements.json"}],
    })
    # 'Komma' is a default rule; pipeline must resolve the bundled source file.
    assert "," in build_pipeline(profile).run("a Komma b").text
