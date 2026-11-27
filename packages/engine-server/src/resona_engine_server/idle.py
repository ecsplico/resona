"""Unload the ASR model after a configurable idle time.

`RESONA_ENGINE_UNLOAD_AFTER` = `never` (default), a number of minutes, or
`0`/`immediately` (unload as soon as the last request finishes). Requests and
open WebSockets count as in flight and block unloading; the next request loads
the model again through `get_transcriber()`.
"""
import asyncio
import gc
import logging
import math
import sys
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from resona_asr_core.registry import release_transcriber, transcriber_loaded

log = logging.getLogger(__name__)

MAX_POLL_SECONDS = 30.0


def parse_unload_after(value: str) -> float | None:
    """Seconds of idle time before unloading, `None` for never.

    Raises ValueError for anything other than `never`, `immediately` or a
    non-negative number of minutes.
    """
    text = value.strip().lower()
    if text in ("", "never"):
        return None
    if text == "immediately":
        return 0.0
    try:
        minutes = float(text)
    except ValueError:
        minutes = math.nan
    if not math.isfinite(minutes) or minutes < 0:
        raise ValueError(
            f"RESONA_ENGINE_UNLOAD_AFTER must be 'never', 'immediately' or a "
            f"number of minutes >= 0, got {value!r}"
        )
    return minutes * 60.0


def free_accelerator_memory() -> None:
    """Collect garbage and return cached accelerator memory to the system.

    Only touches torch / MLX when the engine already imported them, so an
    engine without them never pays their import.
    """
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:  # a broken CUDA runtime must not stop unloading
            log.warning("torch.cuda.empty_cache failed: %s", exc)
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        clear = getattr(mx, "clear_cache", None) or getattr(
            getattr(mx, "metal", None), "clear_cache", None
        )
        if clear is not None:
            try:
                clear()
            except Exception as exc:
                log.warning("mlx clear_cache failed: %s", exc)


class IdleUnloader:
    """Tracks model use and releases the transcriber once idle long enough."""

    def __init__(
        self,
        unload_after: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        release: Callable[[], bool] = release_transcriber,
        loaded: Callable[[], bool] = transcriber_loaded,
        free_memory: Callable[[], None] = free_accelerator_memory,
        poll_interval: float | None = None,
    ):
        self.unload_after = unload_after
        self._clock = clock
        self._release = release
        self._loaded = loaded
        self._free_memory = free_memory
        self._lock = threading.Lock()
        self._in_flight = 0
        self._last_use = clock()
        if poll_interval is None and unload_after:
            poll_interval = min(MAX_POLL_SECONDS, max(1.0, unload_after / 4))
        self.poll_interval = poll_interval

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def loaded(self) -> bool:
        return self._loaded()

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1
            self._last_use = self._clock()

    def end(self) -> None:
        with self._lock:
            self._in_flight -= 1
            self._last_use = self._clock()
            if self.unload_after == 0 and self._in_flight == 0:
                self._unload_locked()

    @contextmanager
    def use(self) -> Iterator[None]:
        """Mark one request or socket as in flight for the block's duration."""
        self.begin()
        try:
            yield
        finally:
            self.end()

    def maybe_unload(self) -> bool:
        """Unload when idle for at least `unload_after` with nothing in flight."""
        with self._lock:
            if self.unload_after is None or self._in_flight:
                return False
            if self._clock() - self._last_use < self.unload_after:
                return False
            return self._unload_locked()

    def _unload_locked(self) -> bool:
        if not self._loaded() or not self._release():
            return False
        self._free_memory()
        log.info("ASR model unloaded after idle time; next request reloads it.")
        return True

    async def run(self) -> None:
        """Background loop for the FastAPI lifespan; returns at once for never/0."""
        if not self.poll_interval:
            return
        while True:
            await asyncio.sleep(self.poll_interval)
            self.maybe_unload()
