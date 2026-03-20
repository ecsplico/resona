"""
Backend configuration for whisper-server clients.

Backends are stored in ~/.whisper-server/config.json as a priority-ordered list.
The first reachable backend is used. If a backend has a compose_dir set and no
backend is reachable, docker compose up -d is run in that directory and the
client waits for it to become available.
"""
import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".whisper-server"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class BackendEntry:
    name: str
    api_url: str
    api_key: str = ""
    compose_dir: Optional[str] = None  # if set, can be auto-started via docker compose

    def health_url(self) -> str:
        return self.api_url.rstrip("/") + "/health"


@dataclass
class BackendConfig:
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

    If no backend is reachable and auto_start is True, attempt to start the
    first compose-backed backend via docker compose up -d, then wait up to
    120 seconds for it to become healthy.

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
        if entry.compose_dir:
            _start_compose(entry.compose_dir)
            log.info(f"Waiting for '{entry.name}' to become available (up to 120s)...")
            if _wait_for_backend(entry):
                return entry

    return None
