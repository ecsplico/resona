"""LocalEngine — spawns a local resona-engine subprocess as a fallback transcription engine."""
import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def _find_free_port() -> int:
    """Bind to port 0, let the OS assign a free port, return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


DEFAULT_LOCAL_ENGINE_PORT = int(os.getenv("RESONA_LOCAL_ENGINE_PORT", "7720"))


def probe_local_engine(port: int = DEFAULT_LOCAL_ENGINE_PORT, timeout: float = 0.5) -> bool:
    """True if a resona-engine-server is already reachable on `port`.

    Used to avoid loading an in-process model or spawning a subprocess when
    one's already running — e.g. started manually for development.
    """
    try:
        return httpx.get(f"http://localhost:{port}/health", timeout=timeout).status_code == 200
    except httpx.RequestError:
        return False


class LocalEngine:
    """Context manager that transcribes via HTTP against a resona-engine-server —
    either one already running (attached, nothing to spawn or tear down), or one
    spawned as a subprocess for the lifetime of the context.

    Usage::

        with LocalEngine(model="small", timeout=120) as engine:
            result = engine.transcribe(Path("audio.wav"), language="de")
            print(result["text"])

    On `__enter__`, first probes `RESONA_LOCAL_ENGINE_PORT` (default 7720) —
    if a server is already listening there, attaches to it directly rather
    than loading another copy of the model. Otherwise spawns a subprocess,
    terminated on `__exit__` (or via atexit on unclean exit). No replacements
    or initial_prompt are sent — local fallback mode only.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout: float = 120.0,
        engine: str = "faster-whisper",
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.engine = engine
        self._package = f"resona-engine-{engine}"
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._attached = False
        self._stderr_file = None
        self._http: httpx.Client | None = None
        # Store as attribute before registering — atexit.unregister needs the same object.
        self._atexit_fn = self._shutdown

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "LocalEngine":
        self._http = httpx.Client(timeout=30.0)

        if probe_local_engine():
            self._port = DEFAULT_LOCAL_ENGINE_PORT
            self._attached = True
            return self

        self._port = _find_free_port()

        env = os.environ.copy()
        env.pop("RESONA_ENGINE_KEY", None)
        env["PORT"] = str(self._port)
        if self.model:
            env["DEFAULT_FASTWHISPER_MODEL"] = self.model

        self._stderr_file = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            ["uv", "run", self._package],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
        )
        atexit.register(self._atexit_fn)

        try:
            self._wait_for_health()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self._shutdown()
        atexit.unregister(self._atexit_fn)
        if self._http:
            self._http.close()
        if self._stderr_file:
            self._stderr_file.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, filepath: Path, language: str = "de") -> dict:
        """POST audio to /transcribe. Returns {text, language, segments}.

        initial_prompt and replacements are omitted — no DB in local fallback mode.
        The response never contains 'md'; callers should use 'text'.
        """
        with open(filepath, "rb") as f:
            resp = self._http.post(
                f"http://localhost:{self._port}/transcribe",
                files={"audio_file": (filepath.name, f, "audio/wav")},
                data={"language": language},
                timeout=3600.0,
            )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_health(self) -> None:
        url = f"http://localhost:{self._port}/health"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._stderr_file.seek(0)
                stderr = self._stderr_file.read().decode(errors="replace")
                raise RuntimeError(f"Engine process exited early:\n{stderr}")
            try:
                r = self._http.get(url)
                if r.status_code == 200:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                    return
            except httpx.RequestError:
                pass
            sys.stderr.write(".")
            sys.stderr.flush()
            time.sleep(1.0)

        self._stderr_file.seek(0)
        stderr = self._stderr_file.read().decode(errors="replace")
        raise RuntimeError(
            f"Engine did not become healthy within {self.timeout}s:\n{stderr}"
        )

    def _shutdown(self) -> None:
        proc = self._process
        self._process = None  # prevent double-call
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
