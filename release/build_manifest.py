"""Build a Resona release manifest from built wheels, pushed image digests and model pins.

    uv run python release/build_manifest.py \
        --release-id 0.2.0 --dist dist/ \
        --image ghcr.io/ecsplico/resona-api=sha256:<64 hex> \
        --image ghcr.io/ecsplico/resona-worker=sha256:<64 hex> \
        --models release/models.json --constraints dist/worker-constraints.txt \
        --out dist/release-manifest.json

Every ``*.whl`` in ``--dist`` is pinned by name, version and SHA-256. The
``resona-api`` and ``resona-worker`` wheels must both be present and carry exactly ``--release-id``:
workers report their ``resona-worker`` version as their release, and the hub
reports its ``resona-api`` version. Images must be given by digest; tags such as
``:latest`` or ``:cuda`` are refused. ``--constraints`` (a file in ``--dist``,
from ``uv export --frozen --package resona-worker --all-extras --no-emit-workspace
--no-hashes``) pins the third-party versions ``resona-worker update`` installs.
The result is validated against
:class:`resona_job_core.release.ReleaseManifest` (``release/manifest.schema.json``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from resona_job_core.release import ReleaseManifest

# PEP 427: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
_WHEEL = re.compile(r"^(?P<name>[A-Za-z0-9_.]+)-(?P<version>[^-]+)(-\d[^-]*)?-[^-]+-[^-]+-[^-]+\.whl$")
RELEASE_PACKAGES = ("resona-api", "resona-worker")


class BuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def wheel_pins(dist: Path) -> list[dict]:
    pins = []
    for path in sorted(dist.glob("*.whl")):
        match = _WHEEL.match(path.name)
        if match is None:
            raise BuildError(f"not a wheel file name: {path.name}")
        name = match["name"].replace("_", "-").lower()
        pins.append({"name": name, "version": match["version"], "sha256": _sha256(path)})
    if not pins:
        raise BuildError(f"no wheels in {dist}")
    return pins


def image_pins(values: list[str]) -> list[dict]:
    pins = []
    for value in values:
        name, sep, digest = value.partition("=")
        if not sep:
            raise BuildError(f"--image must be NAME=sha256:DIGEST, got {value!r}")
        pins.append({"name": name, "digest": digest})
    return pins


def build(
    release_id: str,
    dist: Path,
    images: list[str],
    models: Path,
    *,
    constraints: Path | None = None,
    created_at: datetime | None = None,
) -> ReleaseManifest:
    wheels = wheel_pins(dist)
    for package in RELEASE_PACKAGES:
        versions = [w["version"] for w in wheels if w["name"] == package]
        if not versions:
            raise BuildError(f"no {package} wheel in {dist}; a release needs both {' and '.join(RELEASE_PACKAGES)}")
        if versions != [release_id]:
            raise BuildError(f"{package} wheel version {versions} does not match release {release_id}")
    body = {
        "release_id": release_id,
        "created_at": (created_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "wheels": wheels,
        "images": image_pins(images),
        "models": json.loads(models.read_text(encoding="utf-8")),
        "worker_protocol": 1,
    }
    if constraints is not None:
        if constraints.parent.resolve() != dist.resolve():
            raise BuildError(f"--constraints must be a file inside {dist} (it is shipped with the wheels)")
        body["constraints"] = {"name": constraints.name, "sha256": _sha256(constraints)}
    try:
        return ReleaseManifest.model_validate(body)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise BuildError(f"invalid release manifest: {problems}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a pinned Resona release manifest.")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--dist", type=Path, required=True, help="directory with the built wheels")
    parser.add_argument("--image", action="append", default=[], help="NAME=sha256:DIGEST (repeatable)")
    parser.add_argument("--models", type=Path, default=Path(__file__).with_name("models.json"))
    parser.add_argument("--constraints", type=Path, help="uv.lock export inside --dist (third-party pins)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build(args.release_id, args.dist, args.image, args.models, constraints=args.constraints)
    except (BuildError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(manifest.canonical_json() + "\n", encoding="utf-8")
    print(f"wrote {args.out} (sha256 {manifest.digest()})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
