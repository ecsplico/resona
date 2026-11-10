import json
from pathlib import Path

import pytest

from resona_project_worker.title import derive_title

CASES = json.loads(
    (Path(__file__).parents[2] / "shared" / "titleSuggestion.cases.json").read_text("utf-8")
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_derive_title_matches_shared_cases(case):
    assert derive_title(case["text"]) == case["title"]
