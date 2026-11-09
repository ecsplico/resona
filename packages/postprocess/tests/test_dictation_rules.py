"""Bundled German dictation rules and the transcript Markdown profiles."""

import json
import time
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


@pytest.mark.parametrize("terminator", [
    "Absatz", "Punkt", "Doppelpunkt", "Fragezeichen", "Ausrufezeichen",
    ".", "!", "?", ":", "Absatz.", ", Absatz.",
])
def test_heading_ends_at_terminator(terminator: str) -> None:
    sep = "" if terminator[0] in ".!?:," else " "
    text = f"Kapitel Befunde{sep}{terminator} der Rest bleibt Fließtext Punkt Noch ein Satz Punkt"
    assert _rules(text) == "## Befunde\n\nder Rest bleibt Fließtext. Noch ein Satz."


@pytest.mark.parametrize("text", [
    "Kapitel Anamnese der Patient berichtet ohne Ende",
    "Der Befund ist unauffällig, wie im Kapitel zuvor beschrieben.",
    "Die Ergebnisse im Kapitel 3 zeigt die Tabelle.",
    "Wie im letzten Kapitel. Weiter so.",
    "Kapitel Anamnese der Patient kam am 3. Mai.",
])
def test_kapitel_without_short_terminated_title_stays_as_spoken(text: str) -> None:
    assert _rules(text) == text


@pytest.mark.parametrize(("text", "expected"), [
    # A title longer than five words is not a heading, even with a terminator.
    (
        "Kapitel Anamnese der Patient berichtet Komma er habe Schmerzen Punkt",
        "Kapitel Anamnese der Patient berichtet, er habe Schmerzen.",
    ),
    ("Kapitel Eins zwei drei vier fünf sechs Absatz Text", "Kapitel Eins zwei drei vier fünf sechs\n\nText"),
    # Prose use of "Kapitel" after an article or preposition is left alone.
    ("Ich lese das Kapitel und schreibe Absatz Neu", "Ich lese das Kapitel und schreibe\n\nNeu"),
    # A dot followed by a digit is no terminator.
    ("Kapitel Anamnese der Patient kam am 3. Mai Punkt", "Kapitel Anamnese der Patient kam am 3. Mai."),
])
def test_kapitel_heading_is_not_built_from_prose(text: str, expected: str) -> None:
    assert _rules(text) == expected


def test_heading_title_keeps_codes_and_up_to_five_words() -> None:
    assert _rules("Kapitel Diagnose F32.1 Absatz Text") == "## Diagnose F32.1\n\nText"
    assert _rules("Kapitel Psychosoziale Situation und Familie Absatz Text Punkt") == (
        "## Psychosoziale Situation und Familie\n\nText."
    )


def test_whisper_punctuated_commands_do_not_double_punctuation() -> None:
    text = (
        "Zum Zeitpunkt der Aufnahme. Kapitel Anamnese, Absatz. "
        "Der Patient berichtet, Komma, er habe Schmerzen. Punkt."
    )
    assert _rules(text) == (
        "Zum Zeitpunkt der Aufnahme.\n\n## Anamnese\n\nDer Patient berichtet, er habe Schmerzen."
    )


@pytest.mark.parametrize(("text", "expected"), [
    ("Text, Komma weiter", "Text, weiter"),
    ("Text. Punkt", "Text."),
    ("Er sagte Komma.", "Er sagte,"),
    ("Wirklich, Fragezeichen. Ja. Ausrufezeichen!", "Wirklich? Ja!"),
    ("Siehe unten, Doppelpunkt. Liste", "Siehe unten: Liste"),
])
def test_commands_absorb_adjacent_punctuation(text: str, expected: str) -> None:
    assert _rules(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("Kapitel Verlauf Komma stabil", "## Verlauf\n\nstabil"),
    ("Kapitel Verlauf, Komma, stabil.", "## Verlauf\n\nstabil."),
    ("Erster Satz Punkt\nZweiter Satz Komma weiter Punkt", "Erster Satz.\nZweiter Satz, weiter."),
    ("Liste\nKomma weiter", "Liste\n, weiter"),
])
def test_commands_do_not_eat_newlines(text: str, expected: str) -> None:
    assert _rules(text) == expected


def test_long_whitespace_runs_stay_linear() -> None:
    text = "Anfang" + " " * 50_000 + "Komma Ende" + " " * 50_000 + "Kapitel"
    start = time.perf_counter()
    assert _rules(text) == "Anfang, Ende" + " " * 50_000 + "Kapitel"
    assert time.perf_counter() - start < 2


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
