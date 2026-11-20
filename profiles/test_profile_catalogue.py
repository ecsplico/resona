"""Every catalogue profile is a valid profile named after its file."""

import json
from pathlib import Path

import pytest

from resona_postprocess.profile import Profile

CATALOGUE = Path(__file__).parent
FILES = sorted(CATALOGUE.glob("*.json"))


def test_catalogue_holds_the_documented_profiles() -> None:
    assert [path.stem for path in FILES] == [
        "dictation-medical-de", "document-polish-de", "transcript-markdown-de", "transcript-polish-de",
    ]


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_catalogue_profile_validates(path: Path) -> None:
    data = json.loads(path.read_text("utf-8"))
    profile = Profile.from_dict(data)
    assert profile.name == path.stem
    # Hosted profiles reject 'source': a catalogue profile carries its rules inline.
    assert not any("source" in step for step in profile.steps)
    readme = (CATALOGUE / "README.md").read_text("utf-8")
    assert f"`{path.stem}`" in readme
