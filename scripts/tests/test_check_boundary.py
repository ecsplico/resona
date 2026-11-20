"""scripts/check_boundary.py: hub/app boundary check (HBL)."""

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "check_boundary.py"


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_repository_passes():
    result = _run(_SCRIPT.parents[1])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_hub_source_mentioning_app_internals_fails(tmp_path):
    _write(tmp_path, "packages/api/src/resona_api/clean.py", "x = 1\n")
    _write(tmp_path, "packages/api/src/resona_api/leak.py", "x = 1\n\nURL = 'http://Directus:8055'\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "packages/api/src/resona_api/leak.py:3: mentions Directus (reference app data layer)"
    ]


def test_hub_apps_and_root_build_files_are_scanned(tmp_path):
    _write(tmp_path, "apps/resona-worker/src/resona_worker/x.py", "import resona_project_worker\n")
    _write(tmp_path, "justfile", "mod pwa 'apps/resona-pwa'\nmod app 'apps/resona-pwa/x'\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "apps/resona-worker/src/resona_worker/x.py:1: mentions the app's project worker",
        "justfile:2: mentions the reference app directory",
    ]


def test_hub_tests_and_docs_are_not_scanned(tmp_path):
    _write(tmp_path, "packages/api/tests/test_x.py", "# directus\n")
    _write(tmp_path, "packages/api/README.md", "See apps/resona-pwa.\n")
    _write(tmp_path, "apps/resona-cli/tests/fixtures/x.json", '{"a": "resona-pwa"}\n')
    assert _run(tmp_path).returncode == 0


def test_app_importing_hub_sources_fails(tmp_path):
    _write(tmp_path, "apps/resona-pwa/worker/src/w.py", "from resona_client import ResonaClient\n")
    _write(tmp_path, "apps/resona-pwa/worker/src/bad.py", "import os\nfrom resona_job_core import JobResult\n")
    _write(tmp_path, "apps/resona-verlauf/server/x.ts", 'import a from "../../../../packages/api/x";\n')
    _write(tmp_path, "apps/resona-pwa/Dockerfile", "FROM x\nCOPY apps/resona-mcp/ /src\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "apps/resona-pwa/Dockerfile:2: path-references a hub app under apps/",
        "apps/resona-pwa/worker/src/bad.py:2: imports a hub Python package (use the hub HTTP API or resona-client)",
        "apps/resona-verlauf/server/x.ts:1: path-references hub sources under packages/",
    ]
