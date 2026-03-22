"""
Backend configuration for whisper-server clients.

Backends are stored in ~/.whisper-server/config.json as a priority-ordered list.
The first reachable backend is used.

Auto-start options (tried in order when no backend is reachable):
  compose_dir — runs `docker compose up -d` in that directory
  ssh_host    — opens an SSH port-forward tunnel to a remote machine

SSH tunnel format:
  ssh_host: "[user@]hostname[:port]"   (e.g. "pi@myserver.com" or "myserver.com:2222")
  ssh_remote_port: remote port on the SSH host (defaults to same port as api_url)

  The local port is taken from api_url. Example — remote server runs ws-api on :7000,
  you want to reach it locally on :7000:
    api_url: "http://localhost:7000"
    ssh_host: "user@myserver.com"
  Opens: ssh -N -L 7000:localhost:7000 user@myserver.com
"""
import atexit
import json
import logging
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".whisper-server"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Tracks SSH tunnel subprocesses started this session; cleaned up on exit.
_active_tunnels: dict[str, subprocess.Popen] = {}


def _cleanup_tunnels() -> None:
    for name, proc in list(_active_tunnels.items()):
        if proc.poll() is None:
            log.info(f"Closing SSH tunnel for '{name}'")
            proc.terminate()


atexit.register(_cleanup_tunnels)


@dataclass
class BackendEntry:
    """A single whisper-server backend and its connection parameters.

    Backends are tried in priority order; the first reachable one is used.
    If ``ssh_host`` is set, a local port-forward tunnel is opened before
    connecting. If ``compose_dir`` is set, the backend can be auto-started
    via ``docker compose up -d``.

    Attributes:
        name: Unique identifier shown in ``ws-cli backends list``.
        api_url: Local URL the client connects to (e.g. ``http://localhost:7000``).
            For SSH backends this is the *local* tunnel endpoint.
        api_key: Optional ``X-API-Key`` header value.
        compose_dir: Absolute path to a docker-compose project to auto-start
            when this backend is unreachable.
        ssh_host: SSH host to tunnel through, e.g. ``user@host`` or
            ``user@host:2222``. The port in ``api_url`` is forwarded.
        ssh_remote_port: Remote port on the SSH host. Defaults to the port
            extracted from ``api_url``.
    """

    name: str
    api_url: str
    api_key: str = ""
    compose_dir: Optional[str] = None  # if set, can be auto-started via docker compose
    ssh_host: Optional[str] = None     # "[user@]host[:port]" — opens SSH tunnel when needed
    ssh_remote_port: Optional[int] = None  # remote port (defaults to port in api_url)

    def health_url(self) -> str:
        return self.api_url.rstrip("/") + "/health"


@dataclass
class BackendConfig:
    """Persistent ordered list of configured backends.

    Serialised to ``~/.whisper-server/config.json``. Use :func:`resolve_backend`
    to obtain a ready-to-use :class:`BackendEntry` with auto-start support.

    Attributes:
        backends: Priority-ordered list of :class:`BackendEntry` instances.
            The first reachable entry is selected.
    """

    backends: list[BackendEntry] = field(default_factory=list)

    @classmethod
    def load(cls) -> "BackendConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            backends = [BackendEntry(**b) for b in data.get("backends", [])]
            return cls(backends=backends)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"Could not parse {CONFIG_FILE}: {e}")
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"backends": [asdict(b) for b in self.backends]}, indent=2))

    def get(self, name: str) -> Optional[BackendEntry]:
        return next((b for b in self.backends if b.name == name), None)

    def add(self, entry: BackendEntry) -> None:
        if self.get(entry.name):
            raise ValueError(f"Backend '{entry.name}' already exists")
        self.backends.append(entry)
        self.save()

    def remove(self, name: str) -> None:
        if not self.get(name):
            raise KeyError(f"Backend '{name}' not found")
        self.backends = [b for b in self.backends if b.name != name]
        self.save()


def is_reachable(entry: BackendEntry, timeout: float = 3.0) -> bool:
    """Return True if the backend's /health endpoint responds 200."""
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


def _start_ssh_tunnel(entry: BackendEntry) -> subprocess.Popen:
    """Start an SSH port-forward tunnel for the given backend entry."""
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


def _wait_for_backend(entry: BackendEntry, timeout: float = 120.0, poll: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_reachable(entry):
            return True
        time.sleep(poll)
    return False


def resolve_backend(
    auto_start: bool = True,
    connect_timeout: float = 3.0,
) -> Optional[BackendEntry]:
    """
    Return the first reachable backend from ~/.whisper-server/config.json.

    Resolution order:
    1. Return the first backend that is immediately reachable.
    2. If none reachable and auto_start=True, try to start each backend:
       - SSH backends: open a port-forward tunnel and wait up to 30s.
       - compose backends: run `docker compose up -d` and wait up to 120s.

    Returns None if no backend could be reached or started.
    """
    cfg = BackendConfig.load()
    if not cfg.backends:
        return None

    for entry in cfg.backends:
        if is_reachable(entry, timeout=connect_timeout):
            return entry

    if not auto_start:
        return None

    for entry in cfg.backends:
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
            _start_compose(entry.compose_dir)
            log.info(f"Waiting for '{entry.name}' to become available (up to 120s)...")
            if _wait_for_backend(entry):
                return entry

    return None
