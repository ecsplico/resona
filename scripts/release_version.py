"""Resona release id = the version every workspace package carries.

    python scripts/release_version.py show
    python scripts/release_version.py bump 0.2.0       # every pyproject.toml + uv.lock
    python scripts/release_version.py check [--unreleased]

`check` fails when the workspace packages disagree. With `--unreleased` it also fails
when tag `v<version>` already exists but HEAD is not that tag's commit: such a build
would report the id of an evaluated release while running other code, so the hub
would apply that release's promoted tuples to unevaluated code. Bump first.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc|\.dev)\d+)?$")
_PROJECT_VERSION = re.compile(r'(?m)^(\[project\][^\[]*?^version\s*=\s*")([^"]+)(")', re.DOTALL)


def pyprojects() -> list[Path]:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    files = [ROOT / "pyproject.toml"]
    for pattern in root["tool"]["uv"]["workspace"]["members"]:
        files += sorted(p / "pyproject.toml" for p in ROOT.glob(pattern) if (p / "pyproject.toml").is_file())
    return files


def versions() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): tomllib.loads(p.read_text())["project"]["version"] for p in pyprojects()}


def current() -> str:
    found = set(versions().values())
    if len(found) != 1:
        detail = "\n".join(f"  {path}: {v}" for path, v in versions().items())
        sys.exit(f"workspace versions disagree:\n{detail}")
    return found.pop()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def check(unreleased: bool) -> None:
    version = current()
    if not unreleased:
        print(version)
        return
    tag = f"v{version}"
    if not _git("tag", "--list", tag):
        print(f"{version} (unreleased)")
        return
    if _git("rev-parse", f"{tag}^{{commit}}") != _git("rev-parse", "HEAD"):
        sys.exit(f"release {version} is already tagged ({tag}) but HEAD differs from it: "
                 f"run `python scripts/release_version.py bump <next>`")
    print(f"{version} (HEAD is {tag})")


def bump(new: str) -> None:
    if not VERSION.match(new):
        sys.exit(f"not a release version: {new!r}")
    old = current()
    for path in pyprojects():
        text = path.read_text()
        updated, n = _PROJECT_VERSION.subn(rf"\g<1>{new}\g<3>", text, count=1)
        if n != 1:
            sys.exit(f"{path}: no [project] version line")
        path.write_text(updated)
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    print(f"{old} -> {new} ({len(pyprojects())} packages, uv.lock)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    c = sub.add_parser("check")
    c.add_argument("--unreleased", action="store_true")
    b = sub.add_parser("bump")
    b.add_argument("version")
    args = parser.parse_args()
    if args.cmd == "show":
        print(current())
    elif args.cmd == "check":
        check(args.unreleased)
    else:
        bump(args.version)


if __name__ == "__main__":
    main()
