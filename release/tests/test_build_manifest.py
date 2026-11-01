"""release/build_manifest.py: a release manifest pins both the hub and the worker wheel."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "build_manifest.py"
_spec = importlib.util.spec_from_file_location("build_manifest", _SCRIPT)
build_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_manifest)


def _dist(tmp_path: Path, *names: str) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in names:
        (dist / f"{name.replace('-', '_')}-0.2.0-py3-none-any.whl").write_bytes(name.encode())
    return dist


def _models(tmp_path: Path) -> Path:
    path = tmp_path / "models.json"
    path.write_text("[]")
    return path


def test_manifest_pins_api_and_worker_wheels(tmp_path):
    dist = _dist(tmp_path, "resona-api", "resona-worker", "resona-job-core")

    manifest = build_manifest.build("0.2.0", dist, [], _models(tmp_path))

    assert manifest.wheel("resona-api").version == "0.2.0"
    assert manifest.wheel("resona-worker").version == "0.2.0"


@pytest.mark.parametrize("missing", ["resona-api", "resona-worker"])
def test_missing_release_wheel_is_a_build_error(tmp_path, missing):
    present = [name for name in ("resona-api", "resona-worker") if name != missing]
    dist = _dist(tmp_path, *present, "resona-job-core")

    with pytest.raises(build_manifest.BuildError, match=f"no {missing} wheel"):
        build_manifest.build("0.2.0", dist, [], _models(tmp_path))


def test_release_wheel_version_must_match_release_id(tmp_path):
    dist = _dist(tmp_path, "resona-api", "resona-worker")

    with pytest.raises(build_manifest.BuildError, match="does not match release 0.3.0"):
        build_manifest.build("0.3.0", dist, [], _models(tmp_path))
