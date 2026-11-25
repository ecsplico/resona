"""Every catalogue profile and block is a valid profile named after its file; compositions flatten."""

import json
from pathlib import Path

import pytest

from resona_postprocess.compose import bundled_lookup, chain_lookups, directory_lookup, flatten
from resona_postprocess.profile import Profile

CATALOGUE = Path(__file__).parent
BLOCKS = CATALOGUE / "blocks"
PROFILE_FILES = sorted(CATALOGUE.glob("*.json"))
BLOCK_FILES = sorted(BLOCKS.glob("*.json"))
FILES = PROFILE_FILES + BLOCK_FILES
# What a hub resolves after `resona profiles push profiles/`: the pushed profiles and blocks,
# then the bundled blocks.
LOOKUP = chain_lookups(directory_lookup(CATALOGUE), directory_lookup(BLOCKS), bundled_lookup)
README = (CATALOGUE / "README.md").read_text("utf-8")


def test_catalogue_holds_the_documented_profiles_and_blocks() -> None:
    assert [path.stem for path in PROFILE_FILES] == [
        "dictation-clean-de", "dictation-medical-de", "document-polish-de",
        "transcript-markdown-de", "transcript-polish-de",
    ]
    assert [path.stem for path in BLOCK_FILES] == [
        "headings-medical-de", "names-medical-de", "polish-ocr-de", "polish-punctuation-de",
        "self-correction-de",
    ]


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_catalogue_file_validates(path: Path) -> None:
    data = json.loads(path.read_text("utf-8"))
    profile = Profile.from_dict(data)
    assert profile.name == path.stem
    # Blocks are marked for listings; profiles are not.
    assert profile.block is (path.parent == BLOCKS)
    # Hosted profiles reject 'source': catalogue files carry their rules inline.
    assert not any("source" in step for step in profile.steps)
    # Steps name a model role, never a concrete endpoint id or a model.
    for step in profile.steps:
        if step["type"] in ("llm", "extract"):
            assert step["endpoint"].startswith("@")
            assert "model" not in step
    assert f"`{path.stem}`" in README


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_catalogue_file_flattens(path: Path) -> None:
    flat = flatten(Profile.from_file(path), LOOKUP)
    assert flat.steps
    assert all(step["type"] != "include" and "source" not in step for step in flat.steps)


@pytest.mark.parametrize(("name", "steps"), [
    ("dictation-medical-de", ["names-medical", "headings-medical", "dictation-commands"]),
    ("transcript-markdown-de", ["headings-medical", "dictation-commands"]),
    ("transcript-polish-de", ["headings-medical", "dictation-commands", "transcript-polish"]),
    ("document-polish-de", ["ocr-polish"]),
    ("dictation-clean-de", [
        "filler-words-de", "names-medical", "headings-medical", "dictation-commands", "self-correction",
    ]),
])
def test_composition_flattens_to_the_expected_step_order(name: str, steps: list[str]) -> None:
    flat = flatten(Profile.from_file(CATALOGUE / f"{name}.json"), LOOKUP)
    assert [step["name"] for step in flat.steps] == steps
