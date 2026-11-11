"""``resona-worker`` command line: prepare, run and (on macOS) install one outbound pull worker."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from resona_job_core import OrganizationSettings, default_settings
from resona_job_core.release import ReleaseManifest

from .config import (
    ConfigError,
    PreparedCapability,
    WorkerConfig,
    load_manifest,
    load_token,
    validate_hub_url,
)
from .hub import HubClient, HubError
from .launchd import DEFAULT_LABEL, InstallError, install as install_service, uninstall as uninstall_service
from .prepare import DEFAULT_TIMEOUT_SECONDS, PrepareError, prepare as prepare_profile, write_manifest
from .supervisor import Supervisor
from .update import PROFILE_EXTRAS, Steps, UpdateError, UpdateRequest, run_update

app = typer.Typer(help="Outbound-only pull worker for the Resona hub.", no_args_is_help=True)

_EXIT_CONFIG = 2


@app.callback()
def _root() -> None:
    """Resona worker."""


@app.command()
def run(
    hub: Annotated[str, typer.Option("--hub", help="Hub base URL (https, or loopback http).")],
    token_file: Annotated[Path, typer.Option("--token-file", help="Worker credential, mode 0600.")],
    capabilities_file: Annotated[
        Path, typer.Option("--capabilities-file", help="Manifest written by `resona-worker prepare`.")
    ],
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Disposable per-job scratch directory.")],
    once: Annotated[bool, typer.Option("--once", help="Serve one claim cycle, then exit.")] = False,
) -> None:
    """Claim and execute jobs for this worker until stopped."""
    try:
        manifest = load_manifest(capabilities_file)
        config = WorkerConfig(
            hub_url=hub,
            token=load_token(token_file),
            work_dir=work_dir,
            capabilities=manifest.candidates(),
            diarize=manifest.diarizer is not None,
        )
    except (ConfigError, OSError) as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    code = Supervisor(config).run(once=once)
    sys.exit(code)


def _load_settings(path: Path | None) -> OrganizationSettings:
    if path is None:
        return default_settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read settings file: {exc.__class__.__name__}") from None
    # Accept the bare settings object or the `GET /v1/settings` envelope.
    if isinstance(data, dict) and "settings" in data and "revision" in data:
        data = data["settings"]
    try:
        return OrganizationSettings.model_validate(data)
    except ValidationError:
        raise ConfigError("settings file is not a valid organization settings document") from None


def _load_model_pins(path: Path) -> dict[str, str]:
    try:
        return ReleaseManifest.model_validate_json(path.read_bytes()).model_pins()
    except OSError as exc:
        raise ConfigError(f"cannot read release manifest: {exc.strerror}") from None
    except ValidationError:
        raise ConfigError("release manifest is invalid") from None


def _describe(item: PreparedCapability) -> str:
    c = item.candidate
    stages = ", ".join(f"{stage}={device}" for stage, device in item.stage_accelerators.items())
    memory = f"peak RSS {item.peak_rss_bytes / 2**20:.0f} MiB"
    if item.peak_accelerator_bytes is not None:
        memory += f", peak {c.accelerator} {item.peak_accelerator_bytes / 2**20:.0f} MiB"
    revisions = ", ".join(f"{w.repo}@{w.revision[:12]}" for w in item.weights) or "no hub weights"
    return f"ok {item.kind}: {c.engine} {c.model} [{c.accelerator}] {item.seconds:.1f}s, {memory}; {stages}; {revisions}"


@app.command()
def prepare(
    profile: Annotated[str, typer.Option("--profile", help="Execution profile to prepare, e.g. mac-private.")],
    out: Annotated[Path, typer.Option("--out", help="Capabilities manifest to write.")],
    settings_file: Annotated[
        Path | None,
        typer.Option("--settings-file", help="Organization settings JSON (hub `GET /v1/settings`); default: seeded settings."),
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds per synthetic run.")] = DEFAULT_TIMEOUT_SECONDS,
    release_manifest: Annotated[
        Path | None,
        typer.Option(
            "--release-manifest",
            help="Release manifest whose model pins to prepare (default: the pins of the installed release).",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Never download: fail when a pinned revision is not in the local caches."),
    ] = False,
) -> None:
    """Pin the release's model weights locally and run synthetic jobs for every candidate of a profile.

    Missing pinned weights are downloaded (unless --offline); jobs never download.
    Writes the manifest only when every candidate passes; a failed prepare keeps the previous file.
    """
    try:
        settings = _load_settings(settings_file)
        model_pins = _load_model_pins(release_manifest) if release_manifest is not None else None
        manifest = prepare_profile(
            settings, profile, timeout=timeout, model_pins=model_pins, offline=offline,
            on_tested=lambda item: typer.echo(_describe(item), err=True),
        )
    except (ConfigError, PrepareError) as exc:
        typer.echo(f"prepare failed: {exc}", err=True)
        raise typer.Exit(1) from None
    write_manifest(manifest, out)
    typer.echo(f"prepared {len(manifest.tested)} capability tuple(s) for {profile} -> {out}", err=True)


@app.command()
def capabilities(
    capabilities_file: Annotated[Path, typer.Argument(help="Manifest written by `resona-worker prepare`.")],
) -> None:
    """Print the tested tuples as the JSON list `resona-admin worker approve --capabilities-file` expects."""
    try:
        manifest = load_manifest(capabilities_file)
    except ConfigError as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from None
    typer.echo(json.dumps([c.model_dump(mode="json") for c in manifest.candidates()], indent=2))


@app.command()
def install(
    profile: Annotated[str, typer.Option("--profile", help="Profile the capabilities file was prepared for.")],
    hub: Annotated[str, typer.Option("--hub", help="Hub base URL (https, or loopback http).")],
    token_file: Annotated[Path, typer.Option("--token-file", help="Worker credential, mode 0600.")],
    capabilities_file: Annotated[
        Path, typer.Option("--capabilities-file", help="Manifest written by `resona-worker prepare`.")
    ],
    label: Annotated[str, typer.Option("--label", help="launchd label.")] = DEFAULT_LABEL,
    work_dir: Annotated[
        Path | None, typer.Option("--work-dir", help="Scratch directory (default ~/Library/Caches/resona-worker/<label>).")
    ] = None,
) -> None:
    """Install and start the worker as a launchd user agent (macOS)."""
    try:
        path = install_service(
            profile=profile,
            hub=hub,
            token_file=token_file,
            capabilities_file=capabilities_file,
            label=label,
            work_dir=work_dir,
        )
    except InstallError as exc:
        typer.echo(f"install failed: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from None
    typer.echo(f"installed {label} ({path})", err=True)


@app.command()
def uninstall(
    label: Annotated[str, typer.Option("--label", help="launchd label.")] = DEFAULT_LABEL,
) -> None:
    """Stop the launchd user agent and remove its plist."""
    try:
        removed = uninstall_service(label=label)
    except InstallError as exc:
        typer.echo(f"uninstall failed: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{'removed' if removed else 'not installed:'} {label}", err=True)


@app.command()
def update(
    version: Annotated[str, typer.Option("--version", help="Release id to install, e.g. 0.2.0.")],
    manifest: Annotated[str, typer.Option("--manifest", help="Release manifest: file path or https URL.")],
    profile: Annotated[str, typer.Option("--profile", help="Execution profile to re-prepare.")],
    hub: Annotated[str, typer.Option("--hub", help="Hub base URL (https, or loopback http).")],
    token_file: Annotated[Path, typer.Option("--token-file", help="Worker credential, mode 0600.")],
    capabilities_file: Annotated[
        Path, typer.Option("--capabilities-file", help="Capabilities file the worker runs with; replaced on success.")
    ],
    work_dir: Annotated[Path, typer.Option("--work-dir", help="The running worker's work directory (drain handshake).")],
    releases_dir: Annotated[
        Path, typer.Option("--releases-dir", help="Per-release environments.")
    ] = Path("~/.resona-worker/releases"),
    wheels: Annotated[
        str | None, typer.Option("--wheels", help="Directory or URL holding the wheels (default: next to the manifest).")
    ] = None,
    extras: Annotated[
        str | None, typer.Option("--extras", help="resona-worker extras to install (default: by profile).")
    ] = None,
    settings_file: Annotated[
        Path | None, typer.Option("--settings-file", help="Organization settings JSON for prepare.")
    ] = None,
    label: Annotated[
        str | None, typer.Option("--label", help="launchd label to restart on the new release (macOS).")
    ] = None,
    drain_timeout: Annotated[float, typer.Option("--drain-timeout", help="Seconds to wait for the current job.")] = 3600.0,
    worker_stopped: Annotated[
        bool, typer.Option("--worker-stopped", help="No worker is running: skip the drain handshake.")
    ] = False,
) -> None:
    """Install an approved or staged release: verify, drain, install, re-prepare, then resume.

    A failed install or prepare keeps the previous release and capabilities file and exits 1.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        token = load_token(token_file)
        validate_hub_url(hub)
    except (ConfigError, OSError) as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from None
    request = UpdateRequest(
        release_id=version,
        manifest_source=manifest,
        profile=profile,
        capabilities_file=capabilities_file.expanduser().absolute(),
        work_dir=work_dir.expanduser().absolute(),
        releases_dir=releases_dir.expanduser().absolute(),
        extras=PROFILE_EXTRAS.get(profile, "") if extras is None else extras,
        wheels_source=wheels,
        settings_file=settings_file,
        drain_timeout=drain_timeout,
        worker_running=not worker_stopped,
    )

    def restart(python: Path) -> None:
        install_service(
            profile=profile, hub=hub, token_file=token_file, capabilities_file=request.capabilities_file,
            label=label, work_dir=request.work_dir, python=str(python),
        )

    client = HubClient(hub, token)
    try:
        python = run_update(request, client, Steps(restart=restart if label else None))
    except (UpdateError, InstallError, HubError) as exc:
        typer.echo(f"update failed: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        client.close()
    typer.echo(f"release {version} installed and prepared ({python})", err=True)
    if not label:
        stopped = "" if worker_stopped else "the running worker exits instead of resuming; "
        typer.echo(
            f"no --label service to restart: {stopped}start the new release with: {python} -m resona_worker.cli run "
            f"--hub {hub} --token-file {token_file} --capabilities-file {request.capabilities_file} "
            f"--work-dir {request.work_dir}",
            err=True,
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
