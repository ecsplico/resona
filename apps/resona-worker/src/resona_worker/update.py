"""``resona-worker update``: move one worker to a pinned, hub-known release.

Run on the worker machine by its operator; the hub cannot trigger it. Order:

1. Load the release manifest (file or https URL) and check it names ``--version``.
2. Ask the hub (worker credential) about the release: it must be staged or approved
   for this worker's organization, with the same manifest digest.
3. Download every pinned wheel and verify its SHA-256, before touching the worker.
4. Drain: the running supervisor finishes its current job and stops claiming.
5. Install the wheels into a fresh per-release environment ``<releases-dir>/<id>/venv``.
6. Run ``prepare`` from that environment with the manifest's model pins, into a new
   capabilities file.
7. Only if prepare succeeds: atomically replace the capabilities file, point
   ``<releases-dir>/current`` at the new release and restart the service on it.
8. Release the drain. On any failure after step 4 the previous environment,
   capabilities file and service are untouched, so the old release simply resumes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from resona_job_core.release import ReleaseManifest, WheelPin

from resona_worker import drain
from resona_worker.config import ConfigError, load_manifest, validate_hub_url
from resona_worker.hub import HubClient, ReleaseStatus

log = logging.getLogger("resona_worker.update")

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_WHEEL_BYTES = 200 * 1024 * 1024
CURRENT = "current"
# Third-party versions from uv.lock, as shipped with the release (manifest ``constraints``).
LOCKED_CONSTRAINTS = "locked-constraints.txt"
# Optional dependency sets of the resona-worker wheel each native profile needs.
PROFILE_EXTRAS = {
    "mac-private": "apple,documents",
    "private-auto": "apple,documents",
    "cpu-private": "audio-cpu,documents",
    "linux-cuda": "cuda,documents",
}
_CHUNK = 1024 * 1024


class UpdateError(RuntimeError):
    """The update was refused or failed; the previous release stays in place."""


def wheel_filename(pin: WheelPin) -> str:
    """Resona wheels are pure Python, so the file name follows from name and version."""
    return f"{pin.name.replace('-', '_')}-{pin.version}-py3-none-any.whl"


def _is_url(source: str) -> bool:
    return urlsplit(source).scheme in ("http", "https")


def _join(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name}" if _is_url(base) else str(Path(base).expanduser() / name)


def _base_of(source: str) -> str:
    if _is_url(source):
        return source.rsplit("/", 1)[0]
    return str(Path(source).expanduser().absolute().parent)


def fetch(source: str, dest: Path, limit: int) -> None:
    """Copy a file or https/loopback-http URL to ``dest``, refusing more than ``limit`` bytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not _is_url(source):
        path = Path(source).expanduser()
        try:
            if path.stat().st_size > limit:
                raise UpdateError(f"{path.name} exceeds {limit} bytes")
            shutil.copyfile(path, dest)
        except OSError as exc:
            raise UpdateError(f"cannot read {path}: {exc.strerror}") from None
        return
    try:
        validate_hub_url(source)
    except ConfigError as exc:
        raise UpdateError(f"refusing to download from {source}: {exc}") from None
    total = 0
    try:
        with httpx.stream("GET", source, follow_redirects=True, timeout=60.0) as response, dest.open("wb") as fh:
            if response.status_code != 200:
                raise UpdateError(f"download of {source} failed: HTTP {response.status_code}")
            for chunk in response.iter_bytes(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise UpdateError(f"{source} exceeds {limit} bytes")
                fh.write(chunk)
    except httpx.HTTPError as exc:
        raise UpdateError(f"download of {source} failed: {exc.__class__.__name__}") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()


def create_env(env_dir: Path, requirement: str, constraints: Path) -> Path:
    """``uv venv`` + ``uv pip install <requirement> -c <constraints>``; returns the environment's python."""
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv is required to install a release (https://docs.astral.sh/uv/)")
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    python = env_dir / "bin" / "python"
    for argv in (
        [uv, "venv", "--quiet", "--python", version, str(env_dir)],
        [uv, "pip", "install", "--quiet", "--python", str(python), "-c", str(constraints), requirement],
    ):
        done = subprocess.run(argv, check=False, capture_output=True, text=True)
        if done.returncode != 0:
            raise UpdateError(f"uv {argv[1]} failed ({done.returncode}): {done.stderr.strip()[-300:]}")
    return python


def worker_requirement(manifest: ReleaseManifest, wheels: dict[str, Path], extras: str) -> str:
    """The one thing installed: the pinned ``resona-worker`` wheel with the profile's extras."""
    suffix = f"[{extras}]" if extras else ""
    return f"resona-worker{suffix} @ {wheels['resona-worker'].as_uri()}"


def constraints(manifest: ReleaseManifest, wheels: dict[str, Path]) -> list[str]:
    """Every other pinned wheel as a direct-URL constraint: Resona packages the worker
    depends on come from the release, never from an index, and the ones it does not need
    (hub, CLI, other engines) are not installed."""
    return [f"{pin.name} @ {wheels[pin.name].as_uri()}" for pin in manifest.wheels if pin.name != "resona-worker"]


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


def _wait_drained(work_dir: Path, timeout: float) -> None:
    drain.wait_drained(work_dir, timeout=timeout)


@dataclass
class Steps:
    """The side effects of an update, injectable for tests."""

    fetch: Callable[[str, Path, int], None] = fetch
    create_env: Callable[[Path, str, Path], Path] = create_env
    run_prepare: Callable[[list[str]], int] = _run
    request_drain: Callable[[Path], None] = drain.request_drain
    wait_drained: Callable[[Path, float], None] = _wait_drained
    release_drain: Callable[[Path], None] = drain.release_drain
    # Without a service to restart, the drained worker is told to exit instead of resuming
    # on the old code; the operator starts the new release's worker.
    supersede: Callable[[Path], None] = drain.supersede
    restart: Callable[[Path], None] | None = None


@dataclass
class UpdateRequest:
    release_id: str
    manifest_source: str
    profile: str
    capabilities_file: Path
    work_dir: Path
    releases_dir: Path
    extras: str = ""
    wheels_source: str | None = None
    settings_file: Path | None = None
    drain_timeout: float = 3600.0
    worker_running: bool = True


def _check_hub(status: ReleaseStatus | None, manifest: ReleaseManifest) -> None:
    if status is None:
        raise UpdateError(f"release {manifest.release_id} is not staged on the hub; refusing to install it")
    if status.manifest_sha256 != manifest.digest():
        raise UpdateError(
            f"manifest for {manifest.release_id} differs from the one staged on the hub; refusing to install it"
        )
    if not status.bundled and not status.approved_tuples:
        log.warning(
            "release %s is staged but not approved for any profile of this organization: "
            "this worker will only run evaluation jobs for it until an evaluation is promoted",
            manifest.release_id,
        )


def current_release(releases_dir: Path) -> str | None:
    link = releases_dir / CURRENT
    return os.readlink(link) if link.is_symlink() else None


def _swap_current(releases_dir: Path, release_id: str) -> None:
    tmp = releases_dir / f".{CURRENT}.tmp"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(release_id)
    os.replace(tmp, releases_dir / CURRENT)


def _replace_file(source: Path, dest: Path) -> None:
    """Atomically replace ``dest`` with a copy of ``source`` (copy first, same directory)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.update"
    shutil.copyfile(source, tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)


def _download(request: UpdateRequest, hub: HubClient, steps: Steps, staging: Path) -> ReleaseManifest:
    """Steps 1-3: manifest, hub check, verified wheels. Nothing outside ``staging`` changes."""
    manifest_path = staging / "manifest.json"
    steps.fetch(request.manifest_source, manifest_path, MAX_MANIFEST_BYTES)
    try:
        manifest = ReleaseManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError:
        raise UpdateError("release manifest is invalid") from None
    if manifest.release_id != request.release_id:
        raise UpdateError(f"manifest is for release {manifest.release_id}, not {request.release_id}")
    _check_hub(hub.release(request.release_id), manifest)
    if manifest.wheel("resona-worker") is None:
        raise UpdateError("release manifest pins no resona-worker wheel")

    base = request.wheels_source or _base_of(request.manifest_source)
    for pin in manifest.wheels:
        dest = staging / "wheels" / wheel_filename(pin)
        steps.fetch(_join(base, dest.name), dest, MAX_WHEEL_BYTES)
        if _sha256(dest) != pin.sha256:
            raise UpdateError(f"wheel {dest.name} does not match the manifest's sha256")
    if manifest.constraints is not None:
        dest = staging / LOCKED_CONSTRAINTS
        steps.fetch(_join(base, manifest.constraints.name), dest, MAX_MANIFEST_BYTES)
        if _sha256(dest) != manifest.constraints.sha256:
            raise UpdateError(f"{manifest.constraints.name} does not match the manifest's sha256")
    return manifest


def _install_and_prepare(
    request: UpdateRequest, steps: Steps, manifest: ReleaseManifest, target: Path
) -> tuple[Path, Path]:
    """Steps 5-6 inside ``target``; returns (python, new capabilities file)."""
    wheels = {pin.name: target / "wheels" / wheel_filename(pin) for pin in manifest.wheels}
    constraints_file = target / "constraints.txt"
    lines = constraints(manifest, wheels)
    if manifest.constraints is not None:
        lines += (target / LOCKED_CONSTRAINTS).read_text(encoding="utf-8").splitlines()
    constraints_file.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    python = steps.create_env(
        target / "venv", worker_requirement(manifest, wheels, request.extras), constraints_file
    )
    out = target / "capabilities.json"
    argv = [
        str(python), "-m", "resona_worker.cli", "prepare",
        "--profile", request.profile,
        "--out", str(out),
        "--release-manifest", str(target / "manifest.json"),
    ]
    if request.settings_file is not None:
        argv += ["--settings-file", str(request.settings_file)]
    code = steps.run_prepare(argv)
    if code != 0 or not out.exists():
        raise UpdateError(
            f"prepare failed for release {request.release_id} (exit {code}); keeping the previous release"
        )
    try:
        prepared = load_manifest(out)
    except ConfigError as exc:
        raise UpdateError(f"prepare wrote an unusable capabilities file: {exc}") from None
    if prepared.profile != request.profile:
        raise UpdateError(f"prepare wrote capabilities for {prepared.profile}, not {request.profile}")
    return python, out


def run_update(request: UpdateRequest, hub: HubClient, steps: Steps | None = None) -> Path:
    """Perform one update; returns the new environment's python. Raises :class:`UpdateError`."""
    steps = steps or Steps()
    releases_dir = request.releases_dir.expanduser().absolute()
    releases_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if current_release(releases_dir) == request.release_id:
        raise UpdateError(f"release {request.release_id} is already the current release")
    target = releases_dir / request.release_id
    staging = releases_dir / f".staging-{request.release_id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)
    try:
        manifest = _download(request, hub, steps, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if request.worker_running:
        steps.request_drain(request.work_dir)
    installed = False
    try:
        if request.worker_running:
            try:
                steps.wait_drained(request.work_dir, request.drain_timeout)
            except drain.DrainTimeout as exc:
                raise UpdateError(f"{exc}; nothing was installed") from None
        # A venv is not relocatable, so it is built at its final path.
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        try:
            python, new_caps = _install_and_prepare(request, steps, manifest, target)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        # Point of no return: the new release passed prepare on this machine.
        _replace_file(new_caps, request.capabilities_file)
        _swap_current(releases_dir, request.release_id)
        installed = True
        if steps.restart is not None:
            steps.restart(python)
        return python
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if request.worker_running:
            if installed and steps.restart is None:
                steps.supersede(request.work_dir)
            steps.release_drain(request.work_dir)


__all__ = [
    "CURRENT",
    "PROFILE_EXTRAS",
    "Steps",
    "UpdateError",
    "UpdateRequest",
    "create_env",
    "current_release",
    "fetch",
    "constraints",
    "run_update",
    "worker_requirement",
    "wheel_filename",
]
