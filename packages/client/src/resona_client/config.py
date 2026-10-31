"""
Engine configuration for resona clients.

Engines are Resona hub (resona-api) servers stored in ~/.resona/config.json as a
priority-ordered list. The first reachable engine is used. The CLI never calls
cloud STT providers directly; cloud processing happens only on hub-approved
non-private workers.

Legacy entries of ``type: "cloud"`` are dropped on load (with a warning), and
obsolete keys (``type``, ``provider``, ``model``, ``private``, ``options``) on
hub entries are ignored.

Auto-start options (tried in order when no engine is reachable):
  compose_dir — runs `docker compose up -d` in that directory
  ssh_host    — opens an SSH port-forward tunnel to a remote machine

SSH tunnel format:
  ssh_host: "[user@]hostname[:port]"   (e.g. "pi@myserver.com" or "myserver.com:2222")
  ssh_remote_port: remote port on the SSH host (defaults to same port as api_url)

  The local port is taken from api_url. Example — remote server runs resona-api on :7000,
  you want to reach it locally on :7000:
    api_url: "http://localhost:7000"
    ssh_host: "user@myserver.com"
  Opens: ssh -N -L 7000:localhost:7000 user@myserver.com

Migration: if ~/.resona/config.json does not exist but ~/.whisper-server/config.json does,
the old config is automatically copied to the new location on first load.
"""
import atexit
import json
import logging
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".resona"
CONFIG_FILE = CONFIG_DIR / "config.json"

_LEGACY_CONFIG_DIR = Path.home() / ".whisper-server"
_LEGACY_CONFIG_FILE = _LEGACY_CONFIG_DIR / "config.json"

# Tracks SSH tunnel subprocesses started this session; cleaned up on exit.
_active_tunnels: dict[str, subprocess.Popen] = {}


def _cleanup_tunnels() -> None:
    for name, proc in list(_active_tunnels.items()):
        if proc.poll() is None:
            log.info(f"Closing SSH tunnel for '{name}'")
            proc.terminate()


atexit.register(_cleanup_tunnels)


# Keys an EngineEntry accepts; anything else in config.json is ignored.
_ENTRY_KEYS = {"name", "api_url", "api_key", "compose_dir", "ssh_host", "ssh_remote_port"}


@dataclass
class EngineEntry:
    """A single configured Resona hub (resona-api server)."""

    name: str
    api_url: str = ""
    api_key: str = ""
    compose_dir: Optional[str] = None  # if set, can be auto-started via docker compose
    ssh_host: Optional[str] = None     # "[user@]host[:port]" — opens SSH tunnel when needed
    ssh_remote_port: Optional[int] = None  # remote port (defaults to port in api_url)

    def health_url(self) -> str:
        return self.api_url.rstrip("/") + "/health"

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["EngineEntry"]:
        """Build an entry from a config.json dict; None for legacy cloud entries."""
        if raw.get("type") == "cloud":
            log.warning(
                f"Ignoring legacy cloud engine entry '{raw.get('name')}': the CLI no "
                f"longer calls cloud providers directly. Remove it from {CONFIG_FILE}."
            )
            return None
        return cls(**{k: v for k, v in raw.items() if k in _ENTRY_KEYS})


@dataclass
class EngineConfig:
    """Persistent ordered list of configured engines.

    Serialised to ``~/.resona/config.json``. Use :func:`resolve_engine`
    to obtain a ready-to-use :class:`EngineEntry` with auto-start support.

    If ``~/.resona/config.json`` does not exist but the legacy
    ``~/.whisper-server/config.json`` does, it is automatically migrated
    on first load.

    Attributes:
        engines: Priority-ordered list of :class:`EngineEntry` instances.
            The first reachable entry is selected.
    """

    engines: list[EngineEntry] = field(default_factory=list)
    # "auto" => let the CLI pick an environment-appropriate local engine
    # (resona_asr_core.registry.recommended_engine). A concrete name pins it.
    default_engine: str = "auto"
    default_profile: Optional[str] = None

    @classmethod
    def load(cls) -> "EngineConfig":
        # Migration: copy legacy config if new location does not yet exist.
        if not CONFIG_FILE.exists() and _LEGACY_CONFIG_FILE.exists():
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_LEGACY_CONFIG_FILE, CONFIG_FILE)
                log.info(
                    f"Migrated engine config from {_LEGACY_CONFIG_FILE} to {CONFIG_FILE}"
                )
            except Exception as e:
                log.warning(f"Could not migrate legacy config: {e}")

        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            raw_engines = data.get("engines", data.get("backends", []))
            engines: list[EngineEntry] = []
            for raw in raw_engines:
                entry = EngineEntry.from_dict(raw)
                if entry is not None:
                    engines.append(entry)
            default_engine = data.get("default_engine", data.get("default_backend", "auto"))
            default_profile = data.get("default_profile")
            return cls(engines=engines, default_engine=default_engine,
                       default_profile=default_profile)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"Could not parse {CONFIG_FILE}: {e}")
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "engines": [asdict(e) for e in self.engines],
            "default_engine": self.default_engine,
            "default_profile": self.default_profile,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2))

    def get(self, name: str) -> Optional[EngineEntry]:
        return next((e for e in self.engines if e.name == name), None)

    def add(self, entry: EngineEntry) -> None:
        if self.get(entry.name):
            raise ValueError(f"Engine '{entry.name}' already exists")
        self.engines.append(entry)
        self.save()

    def remove(self, name: str) -> None:
        if not self.get(name):
            raise KeyError(f"Engine '{name}' not found")
        self.engines = [e for e in self.engines if e.name != name]
        self.save()


def is_reachable(entry: EngineEntry, timeout: float = 3.0) -> bool:
    """Return True if the engine's /health endpoint responds 200."""
    try:
        headers = {"X-API-Key": entry.api_key} if entry.api_key else {}
        resp = httpx.get(entry.health_url(), headers=headers, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _local_port(api_url: str) -> int:
    parsed = urlparse(api_url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _local_host(api_url: str) -> str:
    return urlparse(api_url).hostname or "localhost"


def _wait_for_port(host: str, port: int, timeout: float = 30.0, poll: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(poll)
    return False


def _start_ssh_tunnel(entry: EngineEntry) -> subprocess.Popen:
    """Start an SSH port-forward tunnel for the given engine entry."""
    local_port = _local_port(entry.api_url)
    remote_port = entry.ssh_remote_port or local_port

    # Parse optional port suffix from ssh_host: "user@host:2222"
    ssh_host = entry.ssh_host or ""
    ssh_opts: list[str] = []
    if ":" in ssh_host.rsplit("@", 1)[-1]:
        # Has explicit SSH port
        host_part, ssh_port = ssh_host.rsplit(":", 1)
        ssh_opts += ["-p", ssh_port]
        ssh_host = host_part

    cmd = [
        "ssh", "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-L", f"{local_port}:localhost:{remote_port}",
        *ssh_opts,
        ssh_host,
    ]
    log.info(f"Opening SSH tunnel for '{entry.name}': {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def _start_compose(compose_dir: str) -> None:
    log.info(f"Starting docker compose in {compose_dir}")
    subprocess.Popen(
        ["docker", "compose", "up", "-d"],
        cwd=compose_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_engine(entry: EngineEntry, timeout: float = 120.0, poll: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_reachable(entry):
            return True
        time.sleep(poll)
    return False


def resolve_engine(
    auto_start: bool = True,
    connect_timeout: float = 3.0,
    name: Optional[str] = None,
) -> Optional[EngineEntry]:
    """Return a reachable hub entry from ~/.resona/config.json.

    Args:
        auto_start: If True, try to start unreachable entries.
        connect_timeout: Per-entry reachability probe timeout.
        name: If given, resolve only the entry with this exact name.

    Entries are usable when /health responds; if not and ``auto_start`` is
    set, an SSH tunnel or docker compose project is started. A configured
    ``compose_dir`` that does not exist is logged and skipped instead of
    raising FileNotFoundError.

    Returns None if no usable engine is found.
    """
    cfg = EngineConfig.load()
    engines = cfg.engines
    if name is not None:
        engines = [e for e in engines if e.name == name]
    if not engines:
        return None

    # 1. Immediately reachable entries.
    for entry in engines:
        if is_reachable(entry, timeout=connect_timeout):
            return entry

    if not auto_start:
        return None

    # 2. Auto-start entries.
    for entry in engines:
        if entry.ssh_host:
            if entry.name not in _active_tunnels:
                proc = _start_ssh_tunnel(entry)
                _active_tunnels[entry.name] = proc
            host = _local_host(entry.api_url)
            port = _local_port(entry.api_url)
            log.info(f"Waiting for SSH tunnel to '{entry.name}' (up to 30s)...")
            if _wait_for_port(host, port, timeout=30.0) and is_reachable(entry):
                return entry
        elif entry.compose_dir:
            if not Path(entry.compose_dir).is_dir():
                log.warning(
                    f"Engine '{entry.name}': compose_dir '{entry.compose_dir}' "
                    f"does not exist — skipping auto-start."
                )
                continue
            _start_compose(entry.compose_dir)
            log.info(f"Waiting for '{entry.name}' to become available (up to 120s)...")
            if _wait_for_engine(entry):
                return entry

    return None
