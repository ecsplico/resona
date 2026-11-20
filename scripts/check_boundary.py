"""Hub/app boundary check (HBL): the hub knows nothing about the reference app, the app
reaches the hub over HTTP only.

    uv run python scripts/check_boundary.py [--root DIR]

Two directions, each one rule set:

- Hub side: source files under ``packages/``, ``apps/resona-cli``, ``apps/resona-worker``,
  ``apps/resona-mcp``, ``clients/hub-ts`` (the published TypeScript client) and the root
  build files (``justfile``, ``pyproject.toml``, ``docker-compose*.yml``, ``.dockerignore``)
  must not mention the app's internals: Directus, the app's Python packages
  (``resona_project_worker``, ``resona_dictations_mcp``) or the app directories. Tests and
  docs are not scanned.
- App side (only while the apps live in this repo, until #97 moves them out): files under
  the consumer apps must not import hub Python packages or path-reference ``packages/``
  or the hub ``apps/``. ``resona_client`` is allowed: the app may depend on hub code only
  through the published ``resona-client`` (and ``@ecsplico/resona-hub-client``).

Consumer apps: ``apps/resona-pwa`` (the reference app) and ``apps/resona-verlauf``.
Verlauf is a Nuxt layer on top of ``../resona-pwa`` that signs in against Directus and
talks to the hub over HTTP, so it is a consumer, not hub code: it gets the app-side
rules, and the hub must not mention it either. Its references into ``../resona-pwa``
stay inside the app side and are fine.

Prints ``path:line: message`` per violation and exits 1 if there is any.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HUB_DIRS = ("packages", "apps/resona-cli", "apps/resona-worker", "apps/resona-mcp", "clients/hub-ts")
HUB_ROOT_FILES = ("justfile", "pyproject.toml", ".dockerignore")
HUB_ROOT_GLOBS = ("docker-compose*.yml",)
APP_DIRS = ("apps/resona-pwa", "apps/resona-verlauf")

# Hub side: the app's internals, case-insensitive.
HUB_FORBIDDEN = (
    (re.compile(r"directus", re.I), "mentions Directus (reference app data layer)"),
    (re.compile(r"resona_project_worker", re.I), "mentions the app's project worker"),
    (re.compile(r"resona_dictations_mcp", re.I), "mentions the app's MCP server"),
    (re.compile(r"resona[-_]pwa", re.I), "mentions the reference app directory"),
    (re.compile(r"resona[-_]verlauf", re.I), "mentions the Verlauf app directory"),
)

# Hub Python import names (src-layout module names of packages/* and the hub apps).
# resona_client is absent on purpose: it is the published client the apps may use.
_HUB_MODULES = (
    r"resona_(?:api|asr_core|engine_\w+|job_core|diarize|cloud_stt|cloud_tts|tts_local"
    r"|postprocess|eval|worker|mcp|cli)"
)
# App side: imports of hub modules and paths into hub sources.
APP_FORBIDDEN = (
    (
        re.compile(rf"^\s*(?:from|import)\s+{_HUB_MODULES}\b"),
        "imports a hub Python package (use the hub HTTP API or resona-client)",
    ),
    (
        re.compile(r"(?:^|[\s\"'`(=:,]|\.\./)packages/[\w*-]"),
        "path-references hub sources under packages/",
    ),
    (
        re.compile(r"apps/resona-(?:cli|worker|mcp)\b"),
        "path-references a hub app under apps/",
    ),
)


@dataclass(frozen=True)
class Allowed:
    path: str  # repo-relative, POSIX
    pattern: re.Pattern[str]  # searched in the offending line
    reason: str


def _allow(path: str, pattern: str, reason: str) -> Allowed:
    return Allowed(path, re.compile(pattern), reason)


# Sanctioned mentions. One entry per mention, each with its reason. Matched by file and
# line content (not line number), so unrelated edits do not invalidate an entry.
ALLOWLIST = (
    # Hub side.
    _allow("justfile", r"^# PWA recipes \(apps/resona-pwa/justfile\)",
           "comment introducing the `just pwa` module"),
    _allow("justfile", r"^mod pwa 'apps/resona-pwa'$",
           "the `just pwa` recipe module: monorepo task runner entry for the app"),
    _allow("justfile", r"^# Verlauf recipes \(apps/resona-verlauf/justfile\)",
           "comment introducing the `just verlauf` module"),
    _allow("justfile", r"^mod verlauf 'apps/resona-verlauf'$",
           "the `just verlauf` recipe module: monorepo task runner entry for the app"),
    _allow("pyproject.toml", r"^# The uv workspace only: apps/resona-pwa",
           "comment explaining why pytest testpaths leave the app out"),
    _allow("pyproject.toml", r'^extend-exclude = \["apps/resona-pwa"',
           "ruff must not lint the app's own (separately managed) Python"),
    _allow("docker-compose.mac.yml", r"^# The PWA data layer \(Directus \+ project worker\)",
           "comment pointing app operators at the app's compose file (docs link)"),
    _allow("docker-compose.mac.yml", r"^# default: see apps/resona-pwa/docker-compose\.backend\.yml",
           "comment pointing app operators at the app's compose file (docs link)"),
    _allow(".dockerignore", r"^# The PWA builds from its own context \(apps/resona-pwa\)",
           "comment explaining the exclusion below"),
    _allow(".dockerignore", r"^apps/resona-pwa/$",
           "keeps the app out of the hub image build context"),
    # App side.
    _allow("apps/resona-pwa/backup/backup.sh", r"^# App data only: the hub backs up its own volume",
           "comment naming where the hub's own backup lives; no code path into it"),
    _allow("apps/resona-pwa/mcp/pyproject.toml", r"^\s*# Same SDK version as resona-mcp",
           "comment explaining the shared MCP SDK pin; no dependency on resona-mcp"),
)

SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".nuxt", ".output", ".data", "dist", "build", "site",
})
# Hub side: tests and docs are not source.
HUB_SKIP_DIRS = SKIP_DIRS | {"tests", "test", "docs"}
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst"})


def _is_hub_test(path: Path) -> bool:
    name = path.name
    return (
        name == "conftest.py"
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or ".spec." in name
        or ".test." in name
    )


def _walk(base: Path, skip_dirs: frozenset[str]):
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for entry in sorted(base.iterdir()):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name not in skip_dirs:
                yield from _walk(entry, skip_dirs)
        elif entry.is_file():
            yield entry


def _lines(path: Path) -> list[str] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None  # binary
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _allowed(rel: str, line: str) -> bool:
    return any(a.path == rel and a.pattern.search(line) for a in ALLOWLIST)


def _scan(root: Path, path: Path, rules) -> list[str]:
    lines = _lines(path)
    if lines is None:
        return []
    rel = path.relative_to(root).as_posix()
    found = []
    for number, line in enumerate(lines, 1):
        for pattern, message in rules:
            if pattern.search(line) and not _allowed(rel, line):
                found.append(f"{rel}:{number}: {message}")
                break
    return found


def hub_files(root: Path):
    for name in HUB_DIRS:
        for path in _walk(root / name, HUB_SKIP_DIRS):
            if path.suffix not in DOC_SUFFIXES and not _is_hub_test(path):
                yield path
    for name in HUB_ROOT_FILES:
        if (root / name).is_file():
            yield root / name
    for pattern in HUB_ROOT_GLOBS:
        yield from sorted(p for p in root.glob(pattern) if p.is_file())


def app_files(root: Path):
    for name in APP_DIRS:
        for path in _walk(root / name, SKIP_DIRS):
            if path.suffix not in DOC_SUFFIXES:
                yield path


def check(root: Path) -> list[str]:
    violations = []
    for path in hub_files(root):
        violations += _scan(root, path, HUB_FORBIDDEN)
    for path in app_files(root):
        violations += _scan(root, path, APP_FORBIDDEN)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root (default: this script's repository)")
    args = parser.parse_args(argv)
    violations = check(args.root.resolve())
    for violation in violations:
        print(violation)
    if violations:
        print(f"check_boundary: {len(violations)} hub/app boundary violation(s); "
              "see scripts/check_boundary.py for the rules and the allowlist", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
