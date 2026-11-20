"""Profile data in the wheel: only what works with no setup (the `default` profile and its rules)."""

from importlib import resources
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "resona_postprocess"


def _json_files(root) -> list[str]:
    found: list[str] = []

    def walk(node, prefix: str) -> None:
        for child in node.iterdir():
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            elif child.name.endswith(".json"):
                found.append(prefix + child.name)

    walk(root, "")
    return sorted(found)


def test_wheel_ships_only_the_default_profile() -> None:
    # The wheel is built from this directory as it is (hatch `packages = ["src/resona_postprocess"]`).
    expected = ["default_replacements.json", "profiles/default.json"]
    assert _json_files(PACKAGE_DIR) == expected
    assert _json_files(resources.files("resona_postprocess")) == expected
