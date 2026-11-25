"""The bundled `default` keeps only the generic dictation commands; their output did not change.

`fixtures/default_golden.json` holds hand-written expected outputs for texts with generic commands
only; `fixtures/pre_split_default_replacements.json` is the rule list `default` used before the
medical headings and name corrections moved to the catalogue profile `dictation-medical-de`.
`default` is a single include of the bundled block `dictation-commands-de`; `bundled_default()`
returns it flattened, with those rules inline.
"""

import json
from importlib import resources
from pathlib import Path

import pytest

from resona_job_core.policy import ExecutionPolicy
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import bundled_default
from resona_postprocess.replacements import apply_replacements

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "default_golden.json").read_text("utf-8"))
PRE_SPLIT_RULES = json.loads((FIXTURES / "pre_split_default_replacements.json").read_text("utf-8"))
DEFAULT_RULES = json.loads(
    resources.files("resona_postprocess").joinpath("default_replacements.json").read_text("utf-8")
)


def _run_default(text: str) -> str:
    return build_pipeline(bundled_default(), policy=ExecutionPolicy(private=True)).run(text).text


@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: case["input"].strip()[:40])
def test_default_output_matches_golden(case: dict) -> None:
    assert _run_default(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: case["input"].strip()[:40])
def test_default_output_is_byte_identical_to_the_pre_split_rules(case: dict) -> None:
    out = _run_default(case["input"])
    assert out.encode("utf-8") == apply_replacements(case["input"], PRE_SPLIT_RULES).encode("utf-8")


def test_default_holds_only_the_generic_commands() -> None:
    # The pre-split list was: 6 name corrections, 4 medical headings, then the generic commands.
    assert DEFAULT_RULES == PRE_SPLIT_RULES[10:]
    patterns = " ".join(rule["pattern"] for rule in DEFAULT_RULES)
    for medical in ("Monique", "Serafin", "Lucy", "Nele", "Mele", "Verlauf", "Medikation", "Psychopathologischer"):
        assert medical not in patterns
    assert _run_default("Monique Punkt") == "Monique."


def test_flattened_default_is_the_dictation_commands_block_inline() -> None:
    raw = json.loads(resources.files("resona_postprocess").joinpath("profiles/default.json").read_text("utf-8"))
    assert raw["steps"] == [{"type": "include", "profile": "dictation-commands-de"}]
    default = bundled_default()
    assert [step["type"] for step in default.steps] == ["replacements"]
    assert "source" not in default.steps[0]
    assert default.steps[0]["rules"] == DEFAULT_RULES == PRE_SPLIT_RULES[10:]
