"""Chunked llm steps (document polish): placeholders, length guard, hard failure, policy."""

import json
from importlib import resources
from types import SimpleNamespace

import pytest

import resona_postprocess.llm as llm_mod
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy, PolicyDenied
from resona_postprocess.llm import PostprocessStepFailed
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import Profile, ProfileError

LOCAL_LLM = Endpoint(
    id="local-llm",
    kind=EndpointKind.LOCAL_LLM,
    model="openai/qwen3.6-27b",
    url="http://127.0.0.1:9292/v1",
    worker_id="wrk_test",
    unload_url="http://127.0.0.1:9292/api/models/unload/qwen3.6-27b",
)


def _policy(*endpoints: Endpoint) -> ExecutionPolicy:
    return ExecutionPolicy(
        private=True, cloud_enabled=False,
        approved_endpoint_ids=tuple(e.id for e in endpoints), endpoints=endpoints,
    )


class ReplyLLM:
    """litellm stand-in whose reply is computed from the user message (the chunk)."""

    def __init__(self, reply):
        self.reply = reply
        self.chunks: list[str] = []
        self.api_keys: list[str | None] = []

    def completion(self, **kwargs):
        chunk = kwargs["messages"][1]["content"]
        self.chunks.append(chunk)
        self.api_keys.append(kwargs.get("api_key"))
        content = self.reply(chunk)
        if isinstance(content, BaseException):
            raise content
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


def _install(monkeypatch, reply) -> ReplyLLM:
    fake = ReplyLLM(reply)
    monkeypatch.setattr(llm_mod, "litellm", fake)
    return fake


def _step(**extra) -> dict:
    step = {"type": "llm", "name": "ocr-polish", "prompt": "Korrigiere OCR-Fehler.",
            "endpoint": "local-llm", "chunk_chars": 1000}
    step.update(extra)
    return step


def _profile(*steps: dict) -> Profile:
    return Profile.from_dict({"name": "polish", "steps": list(steps)})


def _paragraph(n: int) -> str:
    return " ".join(f"Untersuchung{n} fiir Patientin" for _ in range(12))


DOC = "\n\n".join([
    "# Befund", _paragraph(1), "<!-- image -->", _paragraph(2),
    "| Wert | Einheit |\n|---|---|\n| 5 fiir | mg |", "<!-- formula-not-decoded -->",
    _paragraph(3), _paragraph(4), _paragraph(5),
]) + "\n"


def test_placeholders_and_structure_survive_a_correcting_llm(monkeypatch) -> None:
    fake = _install(monkeypatch, lambda chunk: chunk.replace("fiir", "für"))
    pipe = build_pipeline(_profile(_step(preserve_markup=True, max_length_deviation=0.3)), policy=_policy(LOCAL_LLM))

    result = pipe.run(DOC)

    assert len(fake.chunks) > 1
    assert result.warnings == []
    assert result.text == DOC.replace("fiir", "für")
    assert result.text.count("<!-- image -->") == 1
    assert result.text.count("<!-- formula-not-decoded -->") == 1


def test_chunk_that_drops_a_placeholder_is_rejected_and_kept_raw(monkeypatch) -> None:
    _install(monkeypatch, lambda chunk: chunk.replace("fiir", "für").replace("<!-- image -->", "Bild"))
    pipe = build_pipeline(_profile(_step(preserve_markup=True)), policy=_policy(LOCAL_LLM))

    result = pipe.run(DOC)

    assert "<!-- image -->" in result.text
    assert [w for w in result.warnings if w.startswith("polish_chunk_rejected:")]
    assert all(w.endswith(":markup") for w in result.warnings)
    assert "für" in result.text  # other chunks were still corrected


def test_chunk_whose_length_deviates_too_much_is_rejected_and_kept_raw(monkeypatch) -> None:
    text = _paragraph(1) + "\n"
    _install(monkeypatch, lambda chunk: "Zusammenfassung: Befund unauffällig.")
    pipe = build_pipeline(_profile(_step(max_length_deviation=0.3)), policy=_policy(LOCAL_LLM))

    result = pipe.run(text)

    assert result.text == text
    assert result.warnings == ["polish_chunk_rejected:1:length"]


def test_length_within_tolerance_is_accepted(monkeypatch) -> None:
    text = "Die Untersuchnug ergab keinen Befnud.\n"
    _install(monkeypatch, lambda chunk: "Die Untersuchung ergab keinen Befund.")
    pipe = build_pipeline(_profile(_step(max_length_deviation=0.3)), policy=_policy(LOCAL_LLM))

    result = pipe.run(text)

    assert result.text == "Die Untersuchung ergab keinen Befund.\n"
    assert result.warnings == []


def test_reasoning_block_and_wrapping_fence_are_not_taken_into_the_document(monkeypatch) -> None:
    text = "Die Untersuchnug ergab nichts.\n"
    _install(monkeypatch, lambda chunk: "<think>hmm</think>\n```markdown\nDie Untersuchung ergab nichts.\n```")
    pipe = build_pipeline(_profile(_step(max_length_deviation=0.3)), policy=_policy(LOCAL_LLM))

    assert pipe.run(text).text == "Die Untersuchung ergab nichts.\n"


def test_chunk_failing_every_attempt_fails_the_step_instead_of_skipping(monkeypatch) -> None:
    fake = _install(monkeypatch, lambda chunk: ConnectionError("refused"))
    pipe = build_pipeline(_profile(_step(max_attempts=3)), policy=_policy(LOCAL_LLM))

    with pytest.raises(PostprocessStepFailed) as excinfo:
        pipe.run(DOC)

    assert len(fake.chunks) == 3
    assert "chunk 1" in excinfo.value.reason


def test_transient_chunk_failure_is_retried(monkeypatch) -> None:
    calls = {"n": 0}

    def flaky(chunk):
        calls["n"] += 1
        return TimeoutError("stalled") if calls["n"] == 1 else chunk.replace("fiir", "für")

    _install(monkeypatch, flaky)
    pipe = build_pipeline(_profile(_step(max_attempts=3)), policy=_policy(LOCAL_LLM))

    assert pipe.run(DOC).text == DOC.replace("fiir", "für")


def test_non_loopback_local_endpoint_is_denied_before_any_call(monkeypatch) -> None:
    fake = _install(monkeypatch, lambda chunk: chunk)
    remote = Endpoint(id="local-llm", kind=EndpointKind.LOCAL_LLM, model="openai/qwen3.6-27b",
                      url="http://192.168.1.20:9292/v1")

    with pytest.raises(PolicyDenied):
        build_pipeline(_profile(_step()), policy=_policy(remote))

    # A policy that changes after the pipeline was built is still enforced per chunk.
    def deny(*args, **kwargs):
        raise PolicyDenied("llm", "revoked")

    pipe = build_pipeline(_profile(_step()), policy=_policy(LOCAL_LLM))
    monkeypatch.setattr(llm_mod, "check_policy", deny)
    with pytest.raises(PolicyDenied):
        pipe.run(DOC)
    assert fake.chunks == []


def test_bundled_document_polish_profile_runs_long_markdown_in_chunks(monkeypatch) -> None:
    data = json.loads(
        resources.files("resona_postprocess").joinpath("profiles/document-polish-de.json").read_text("utf-8")
    )
    profile = Profile.from_dict(data)
    fake = _install(monkeypatch, lambda chunk: chunk)
    long_doc = "\n\n".join([DOC] * 30)

    result = build_pipeline(profile, policy=_policy(LOCAL_LLM)).run(long_doc)

    assert result.text == long_doc
    assert len(fake.chunks) >= 3
    assert all(len(chunk) <= 12000 for chunk in fake.chunks)


@pytest.mark.parametrize("step", [
    {"type": "llm", "prompt": "p", "endpoint": "e", "max_attempts": 3},
    {"type": "llm", "prompt": "p", "endpoint": "e", "preserve_markup": True},
    {"type": "llm", "prompt": "p", "endpoint": "e", "chunk_chars": 10},
    {"type": "llm", "prompt": "p", "endpoint": "e", "chunk_chars": 5000, "max_attempts": 0},
    {"type": "llm", "prompt": "p", "endpoint": "e", "chunk_chars": 5000, "max_length_deviation": -1},
    {"type": "llm", "prompt": "p", "endpoint": "e", "timeout": 0},
])
def test_invalid_chunking_options_are_rejected(step: dict) -> None:
    with pytest.raises(ProfileError):
        Profile.from_dict({"name": "x", "steps": [step]})


def test_local_endpoint_never_receives_an_ambient_cloud_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-cloud-secret")
    fake = _install(monkeypatch, lambda chunk: chunk)

    build_pipeline(_profile(_step()), policy=_policy(LOCAL_LLM)).run(DOC)

    assert fake.api_keys and "sk-cloud-secret" not in fake.api_keys
    assert all(key for key in fake.api_keys)
