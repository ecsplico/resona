"""German medical dictation rules (catalogue `dictation-medical-de`) and the transcript Markdown profiles.

Catalogue profiles are compositions of blocks (`profiles/blocks/`, bundled blocks). Every rule test
runs the flattened `dictation-medical-de` and checks its output against the rule list the bundled
`default` used before the split (`fixtures/pre_split_default_replacements.json`).
"""

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import resona_postprocess.llm as llm_mod
from resona_job_core import OrganizationSettings
from resona_job_core.policy import Endpoint, EndpointKind, ExecutionPolicy, PolicyDenied
from resona_postprocess.compose import bundled_lookup, chain_lookups, directory_lookup, flatten
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import Profile
from resona_postprocess.replacements import apply_replacements
from resona_postprocess.roles import resolve_roles

CATALOGUE = Path(__file__).resolve().parents[3] / "profiles"
# A hub after `resona profiles push profiles/`: catalogue profiles and blocks, then the bundled blocks.
CATALOGUE_LOOKUP = chain_lookups(
    directory_lookup(CATALOGUE), directory_lookup(CATALOGUE / "blocks"), bundled_lookup,
)
PRE_SPLIT_RULES = json.loads(
    (Path(__file__).parent / "fixtures" / "pre_split_default_replacements.json").read_text("utf-8")
)
TRANSCRIPT_PROFILES = ("transcript-markdown-de", "transcript-polish-de")


def _catalogue_profile(name: str) -> Profile:
    """The catalogue profile ``name``, flattened the way the hub flattens it at admission."""
    return flatten(Profile.from_file(CATALOGUE / f"{name}.json"), CATALOGUE_LOOKUP)


def _flat_rules(profile: Profile) -> list[dict]:
    return [rule for step in profile.steps if step["type"] == "replacements" for rule in step["rules"]]


MEDICAL_RULES = _flat_rules(_catalogue_profile("dictation-medical-de"))

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
    out = apply_replacements(text, MEDICAL_RULES)
    # dictation-medical-de produces what `default` produced before the split.
    assert out == apply_replacements(text, PRE_SPLIT_RULES)
    return out


def _with_roles(profile: Profile, **roles: str) -> Profile:
    return resolve_roles(profile, OrganizationSettings(endpoints=[LOCAL_LLM], model_roles=roles))


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


def test_dictation_medical_profile_is_the_pre_split_default() -> None:
    # Name corrections, the medical headings, then the generic commands, in the pre-split order.
    assert MEDICAL_RULES == PRE_SPLIT_RULES
    profile = _catalogue_profile("dictation-medical-de")
    assert [step["type"] for step in profile.steps] == ["replacements"] * 3
    pipe = build_pipeline(profile, policy=ExecutionPolicy(private=True))
    assert pipe.run("Monique Punkt Kapitel Verlauf stabil Punkt").text == "Monic.\n\n## Verlauf\n\nstabil."


@pytest.mark.parametrize("name", TRANSCRIPT_PROFILES)
def test_transcript_profiles_reuse_the_medical_command_rules(name: str) -> None:
    rules = _flat_rules(_catalogue_profile(name))
    # The flattened rules are dictation-medical-de without its leading name corrections
    # (one `\bName\b` rule each).
    head, tail = MEDICAL_RULES[:-len(rules)], MEDICAL_RULES[-len(rules):]
    assert tail == rules
    assert head and all(r["pattern"].startswith("\\b") and r["pattern"].endswith("\\b") for r in head)
    assert not any("Monique" in r["pattern"] for r in rules)


def test_transcript_markdown_profile_runs_without_llm() -> None:
    profile = _catalogue_profile("transcript-markdown-de")
    pipe = build_pipeline(profile, policy=ExecutionPolicy(private=True))
    assert pipe.run("Kapitel Anamnese Absatz Text Punkt").text == "## Anamnese\n\nText."


def test_transcript_polish_profile_needs_the_polish_role_and_polishes_after_rules(monkeypatch) -> None:
    profile = _catalogue_profile("transcript-polish-de")
    assert profile.steps[-1]["endpoint"] == "@polish"
    with pytest.raises(PolicyDenied):
        build_pipeline(_with_roles(profile, polish="local-llm"), policy=ExecutionPolicy(private=True))
    profile = _with_roles(profile, polish="local-llm")

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


# dictation-clean-de: filler-words-de runs on the raw transcript, before the names, headings and
# commands; the llm step self-correction-de comes last.

def _clean_rules(text: str) -> str:
    """``text`` through the flattened ``dictation-clean-de`` without its llm step."""
    profile = _catalogue_profile("dictation-clean-de")
    rules_only = replace(profile, steps=[step for step in profile.steps if step["type"] == "replacements"])
    return build_pipeline(rules_only, policy=ExecutionPolicy(private=True)).run(text).text


@pytest.mark.parametrize(("text", "expected"), [
    # Filler at the start of the dictation and after a spoken full stop.
    (
        "Äh der Patient berichtet Komma ähm er habe seit drei Tagen Kopfschmerzen Punkt",
        "der Patient berichtet, er habe seit drei Tagen Kopfschmerzen.",
    ),
    ("Erster Satz Punkt Ähm Zweiter Satz Punkt", "Erster Satz. Zweiter Satz."),
    # Between commas Whisper set around a hesitation: one adjacent comma goes with the filler.
    ("Der Befund ist, äh, unauffällig Punkt", "Der Befund ist, unauffällig."),
    ("Monique äh berichtet, mhm, dass es besser geht Punkt", "Monic berichtet, dass es besser geht."),
    ("Der Patient, ähm, berichtet über Schlafstörungen, äh. Er schläft schlecht.",
     "Der Patient, berichtet über Schlafstörungen. Er schläft schlecht."),
    # Before a spoken comma or full stop, repeated, after a heading, inside a list.
    ("Er sagte äh Komma es gehe Punkt", "Er sagte, es gehe."),
    ("Der Patient äh äh berichtet Punkt", "Der Patient berichtet."),
    ("Kapitel Medikation Absatz Ramipril 5 mg ähm Punkt", "## Medikation\n\nRamipril 5 mg."),
    ("Kapitel Verlauf ähm der Patient ist stabil Punkt", "## Verlauf\n\nder Patient ist stabil."),
    ("Vorher Punkt ähm Absatz Weiter Punkt", "Vorher.\n\nWeiter."),
    (
        "Kapitel Diagnosen Doppelpunkt Anstrich ähm Hypertonie Anstrich Diabetes Absatz Ende Punkt",
        "## Diagnosen\n\n- Hypertonie\n- Diabetes\n\nEnde.",
    ),
])
def test_dictation_clean_removes_fillers_inside_dictations(text: str, expected: str) -> None:
    assert _clean_rules(text) == expected


@pytest.mark.parametrize("text", [
    "Die Ähnlichkeit mit dem Vorbefund ist ähnlich hoch.",
    "Die Ähre und das Ähmlichkeitsmaß bleiben.",
    "Ähmlichkeit bleibt.",
    "Er hummelt durch die Hmmelreich-Straße.",
])
def test_dictation_clean_keeps_words_that_contain_a_filler(text: str) -> None:
    assert _clean_rules(text) == text


def test_dictation_clean_rules_are_the_medical_rules_after_the_fillers() -> None:
    # Without fillers in the text, dictation-clean-de's rules give what dictation-medical-de gives.
    text = "Monique Punkt Kapitel Verlauf stabil Komma Kapitel Befunde Absatz Klammer auf gut Klammer zu Punkt"
    assert _clean_rules(text) == _rules(text)


def test_self_correction_runs_last_on_the_cleanup_role(monkeypatch) -> None:
    profile = _catalogue_profile("dictation-clean-de")
    step = profile.steps[-1]
    assert step["endpoint"] == "@cleanup"
    assert step["temperature"] == 0 and step["preserve_markup"] is True
    assert "nein, streich das" in step["prompt"] and "Korrektur" in step["prompt"]
    assert "unverändert" in step["prompt"]

    seen: list[str] = []

    class Correct:
        def completion(self, **kwargs):
            chunk = kwargs["messages"][1]["content"]
            seen.append(chunk)
            reply = chunk.replace("5 mg, nein, streich das, ", "")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", Correct())
    pipe = build_pipeline(_with_roles(profile, cleanup="local-llm"), policy=LOCAL_POLICY)
    result = pipe.run(
        "Kapitel Medikation Absatz ähm Ramipril 5 mg Komma nein Komma streich das Komma 10 mg täglich Punkt"
    )

    assert seen == ["## Medikation\n\nRamipril 5 mg, nein, streich das, 10 mg täglich."]
    assert result.text == "## Medikation\n\nRamipril 10 mg täglich."
    assert result.warnings == []


def test_self_correction_keeps_the_text_when_the_model_rewrites_too_much(monkeypatch) -> None:
    profile = _with_roles(_catalogue_profile("dictation-clean-de"), cleanup="local-llm")

    class Summarize:
        def completion(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Stabil."))], usage=None)

    monkeypatch.setattr(llm_mod, "litellm", Summarize())
    result = build_pipeline(profile, policy=LOCAL_POLICY).run(
        "Kapitel Verlauf der Patient ist stabil Komma Korrektur Komma weitgehend stabil Punkt"
    )

    # A reply far off the input length is rejected; the chunk stays as the rules left it.
    assert result.text == "## Verlauf\n\nder Patient ist stabil, Korrektur, weitgehend stabil."
    assert result.warnings == ["polish_chunk_rejected:1:length"]
