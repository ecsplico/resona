"""Bundled German dictation rules and the transcript Markdown profiles."""

import json
from importlib import resources
from types import SimpleNamespace

import pytest

import resona_postprocess.llm as llm_mod
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy, PolicyDenied
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import resolve_profile
from resona_postprocess.replacements import apply_replacements

PACKAGE = resources.files("resona_postprocess")
DEFAULT_RULES = json.loads(PACKAGE.joinpath("default_replacements.json").read_text("utf-8"))
TRANSCRIPT_PROFILES = ("transcript-markdown-de", "transcript-polish-de")

LOCAL_LLM = Endpoint(
    id="local-llm",
    kind=EndpointKind.LOCAL_LLM,
    model="openai/qwen3.6-27b",
    url="http://127.0.0.1:9292/v1",
)
LOCAL_POLICY = ExecutionPolicy(
    private=True, cloud_enabled=False, approved_endpoint_ids=("local-llm",), endpoints=(LOCAL_LLM,),
)


def _rules(text: str) -> str:
    return apply_replacements(text, DEFAULT_RULES)


def _bundled_profile(name: str):
    with resources.as_file(PACKAGE.joinpath(f"profiles/{name}.json")) as path:
        return resolve_profile(str(path), path.parent)


@pytest.mark.parametrize("text", [
    "Zum Zeitpunkt der Aufnahme",
    "Vom Standpunkt des Kommandanten aus",
    "Die Kamele im Absatzgebiet",
    "Ein Doppelpunktsatz mit Kapitelübersicht",
])
def test_commands_inside_words_are_untouched(text: str) -> None:
    assert _rules(text) == text


def test_issue_example_heading_paragraph_and_punctuation() -> None:
    text = "Zum Zeitpunkt der Aufnahme Kapitel Anamnese Absatz der Patient berichtet Komma er habe Schmerzen Punkt"
    assert _rules(text) == (
        "Zum Zeitpunkt der Aufnahme\n\n## Anamnese\n\nder Patient berichtet, er habe Schmerzen."
    )


@pytest.mark.parametrize("terminator", ["Absatz", "Punkt", "Doppelpunkt", ".", "!", "?", ":", "Absatz."])
def test_heading_ends_at_terminator(terminator: str) -> None:
    sep = "" if terminator[0] in ".!?:" else " "
    text = f"Kapitel Befunde{sep}{terminator} der Rest bleibt Fließtext Punkt Noch ein Satz Punkt"
    assert _rules(text) == "## Befunde\n\nder Rest bleibt Fließtext. Noch ein Satz."


def test_heading_without_terminator_does_not_swallow_the_text() -> None:
    assert "##" not in _rules("Kapitel Anamnese der Patient berichtet ohne Ende")


def test_absatz_is_a_markdown_paragraph() -> None:
    assert _rules("Erster Satz Punkt Absatz Zweiter Satz Punkt") == "Erster Satz.\n\nZweiter Satz."


def test_lists_after_heading_and_text() -> None:
    text = "Kapitel Diagnosen Doppelpunkt Anstrich Hypertonie Anstrich Diabetes Absatz Ende Punkt"
    assert _rules(text) == "## Diagnosen\n\n- Hypertonie\n- Diabetes\n\nEnde."


@pytest.mark.parametrize(("spoken", "heading"), [
    ("Verlauf", "Verlauf"),
    ("Medikation", "Medikation"),
    ("Psychopathologischer Befund", "Psychopathologischer Befund"),
    ("Prozedere", "Procedere"),
])
def test_medical_headings_end_without_terminator(spoken: str, heading: str) -> None:
    text = f"Vorher Punkt Kapitel {spoken} der Patient ist stabil Punkt"
    assert _rules(text) == f"Vorher.\n\n## {heading}\n\nder Patient ist stabil."


def test_medical_heading_swallows_a_spoken_terminator() -> None:
    assert _rules("Kapitel Medikation Absatz Ramipril Punkt") == "## Medikation\n\nRamipril."


def test_brackets_and_punctuation_after_brackets() -> None:
    assert _rules("Text Klammer auf Inhalt Klammer zu Punkt") == "Text (Inhalt)."


def test_question_and_exclamation_commands() -> None:
    assert _rules("Wirklich Fragezeichen Ja Ausrufezeichen") == "Wirklich? Ja!"


@pytest.mark.parametrize("name", TRANSCRIPT_PROFILES)
def test_transcript_profiles_reuse_the_default_command_rules(name: str) -> None:
    profile = _bundled_profile(name)
    rules = profile.steps[0]["rules"]
    # Inline rules (hosted profiles reject 'source'); they must stay the command part of the defaults.
    assert DEFAULT_RULES[-len(rules):] == rules
    assert not any("Monique" in r["pattern"] for r in rules)


def test_transcript_markdown_profile_runs_without_llm() -> None:
    profile = _bundled_profile("transcript-markdown-de")
    pipe = build_pipeline(profile, policy=ExecutionPolicy(private=True))
    assert pipe.run("Kapitel Anamnese Absatz Text Punkt").text == "## Anamnese\n\nText."


def test_transcript_polish_profile_needs_local_llm_and_polishes_after_rules(monkeypatch) -> None:
    profile = _bundled_profile("transcript-polish-de")
    with pytest.raises(PolicyDenied):
        build_pipeline(profile, policy=ExecutionPolicy(private=True))

    seen: list[str] = []

    class Capitalize:
        def completion(self, **kwargs):
            chunk = kwargs["messages"][1]["content"]
            seen.append(chunk)
            reply = chunk.replace("\n\nder", "\n\nDer")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", Capitalize())
    result = build_pipeline(profile, policy=LOCAL_POLICY).run("Kapitel Anamnese Absatz der Patient Punkt")

    assert seen == ["## Anamnese\n\nder Patient."]
    assert result.text == "## Anamnese\n\nDer Patient."
    assert result.warnings == []
