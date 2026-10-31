"""macOS user service: install/uninstall the worker as a launchd agent (``gui/<uid>`` domain).

The agent runs ``resona-worker run`` with the hub URL, the token *file path*, the
prepared capabilities manifest and a private work directory. The token itself never
appears in the plist, its arguments or its environment. The worker only opens
outbound HTTPS (or loopback HTTP) connections; it has no listener.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from resona_worker.config import ConfigError, load_manifest, load_token, validate_hub_url

DEFAULT_LABEL = "de.ecsplico.resona-worker"
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
_SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
# Non-secret locations the worker needs to find the prepared model cache.
_PASS_ENV = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "RESONA_MODELS_DIR", "VOICEBOX_MODELS_DIR")

Launchctl = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class InstallError(RuntimeError):
    """The service could not be installed or removed. Messages carry no credential material."""


def run_launchctl(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False, timeout=60)


@dataclass(frozen=True)
class Launchd:
    """Where agent plists/logs live and how ``launchctl`` is invoked (injectable for tests)."""

    agents_dir: Path
    logs_dir: Path
    cache_dir: Path
    uid: int
    launchctl: Launchctl = run_launchctl

    @classmethod
    def for_current_user(cls) -> Launchd:
        home = Path.home()
        return cls(
            agents_dir=home / "Library" / "LaunchAgents",
            logs_dir=home / "Library" / "Logs" / "resona-worker",
            cache_dir=home / "Library" / "Caches" / "resona-worker",
            uid=os.getuid(),
        )

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def plist_path(self, label: str) -> Path:
        return self.agents_dir / f"{label}.plist"

    def is_loaded(self, label: str) -> bool:
        return self.launchctl(["print", f"{self.domain}/{label}"]).returncode == 0


def _check_label(label: str) -> str:
    if not _LABEL.match(label):
        raise InstallError("label must be a reverse-DNS style name (letters, digits, '.', '-')")
    return label


def _service_path(environ: Mapping[str, str]) -> str:
    dirs: list[str] = []
    ffmpeg = shutil.which("ffmpeg", path=environ.get("PATH"))
    if ffmpeg:  # audio decoding shells out to ffmpeg
        dirs.append(str(Path(ffmpeg).parent))
    dirs.extend(d for d in _SYSTEM_PATH if d not in dirs)
    return ":".join(dirs)


def build_plist(
    *,
    label: str,
    hub: str,
    token_file: Path,
    capabilities_file: Path,
    work_dir: Path,
    log_file: Path,
    python: str,
    environ: Mapping[str, str],
) -> dict[str, object]:
    env = {"PATH": _service_path(environ), "PYTHONUNBUFFERED": "1"}
    env.update({name: environ[name] for name in _PASS_ENV if environ.get(name)})
    return {
        "Label": label,
        "ProgramArguments": [
            python,
            "-m",
            "resona_worker.cli",
            "run",
            "--hub",
            hub,
            "--token-file",
            str(token_file),
            "--capabilities-file",
            str(capabilities_file),
            "--work-dir",
            str(work_dir),
        ],
        "EnvironmentVariables": env,
        "WorkingDirectory": str(work_dir),
        "RunAtLoad": True,
        # Restart after crashes or hub-side failures; a clean exit (e.g. Ctrl-C) stays stopped.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Standard",
        "Umask": 0o077,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }


def install(
    *,
    profile: str,
    hub: str,
    token_file: Path,
    capabilities_file: Path,
    label: str = DEFAULT_LABEL,
    work_dir: Path | None = None,
    launchd: Launchd | None = None,
    python: str = sys.executable,
    environ: Mapping[str, str] = os.environ,
    platform_name: str = sys.platform,
) -> Path:
    """Write the agent plist and bootstrap it into the user's GUI domain. Returns the plist path."""
    if platform_name != "darwin":
        raise InstallError("launchd install is macOS-only; Linux workers use docker-compose.worker.yml")
    label = _check_label(label)
    launchd = launchd or Launchd.for_current_user()
    try:
        hub = validate_hub_url(hub)
        token_file = token_file.expanduser().absolute()
        load_token(token_file)  # mode/type check only; the value is not kept
        capabilities_file = capabilities_file.expanduser().absolute()
        manifest = load_manifest(capabilities_file)
    except ConfigError as exc:
        raise InstallError(str(exc)) from None
    except OSError as exc:
        raise InstallError(f"cannot read {exc.filename}: {exc.strerror}") from None
    if manifest.profile != profile:
        raise InstallError(
            f"capabilities file was prepared for profile {manifest.profile!r}, not {profile!r}; "
            f"run `resona-worker prepare --profile {profile}`"
        )

    work_dir = (work_dir or launchd.cache_dir / label).expanduser().absolute()
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    launchd.logs_dir.mkdir(parents=True, exist_ok=True)
    launchd.agents_dir.mkdir(parents=True, exist_ok=True)
    plist = build_plist(
        label=label,
        hub=hub,
        token_file=token_file,
        capabilities_file=capabilities_file,
        work_dir=work_dir,
        log_file=launchd.logs_dir / f"{label}.log",
        python=python,
        environ=environ,
    )

    path = launchd.plist_path(label)
    if launchd.is_loaded(label):
        launchd.launchctl(["bootout", f"{launchd.domain}/{label}"])
    _write_plist(path, plist)
    done = launchd.launchctl(["bootstrap", launchd.domain, str(path)])
    if done.returncode != 0:
        path.unlink(missing_ok=True)
        raise InstallError(f"launchctl bootstrap failed ({done.returncode}): {done.stderr.strip()[:300]}")
    return path


def uninstall(*, label: str = DEFAULT_LABEL, launchd: Launchd | None = None) -> bool:
    """Stop and remove the agent. Returns False when nothing was installed under ``label``."""
    label = _check_label(label)
    launchd = launchd or Launchd.for_current_user()
    path = launchd.plist_path(label)
    loaded = launchd.is_loaded(label)
    if loaded:
        done = launchd.launchctl(["bootout", f"{launchd.domain}/{label}"])
        if done.returncode != 0 and launchd.is_loaded(label):
            raise InstallError(f"launchctl bootout failed ({done.returncode}): {done.stderr.strip()[:300]}")
    existed = path.exists()
    path.unlink(missing_ok=True)
    return loaded or existed


def _write_plist(path: Path, plist: dict[str, object]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(plist, handle)
        os.chmod(tmp, 0o644)  # launchd rejects group/world-writable agent plists
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


__all__ = ["DEFAULT_LABEL", "InstallError", "Launchd", "build_plist", "install", "run_launchctl", "uninstall"]
