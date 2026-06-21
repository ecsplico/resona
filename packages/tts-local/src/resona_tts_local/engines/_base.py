"""Shared helpers for local TTS engines."""
import importlib
from types import ModuleType

from ..errors import EngineUnavailableError


def lazy_import(module: str, *, install: str) -> ModuleType:
    """Import a native model library, mapping ImportError to a clear hint.

    Args:
        module: the importable module name (e.g. ``"kokoro"``).
        install: the command that installs it (shown in the error).
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # native lib not installed
        raise EngineUnavailableError(
            f"'{module.split('.')[0]}' is not installed. Install it with: {install}"
        ) from exc


def torch_device(*, force_cpu_on_mac: bool = False) -> str:
    """Pick a torch device: cuda if present, else cpu.

    Several engines (Chatterbox, TADA) have no working MPS path, so
    ``force_cpu_on_mac`` keeps them on the CPU on Apple Silicon.
    """
    import sys

    if force_cpu_on_mac and sys.platform == "darwin":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and (
            torch.backends.mps.is_available()
        ):
            return "mps"
    except ImportError:
        pass
    return "cpu"


def seed_torch(seed: int | None) -> None:
    """Seed torch RNGs if a seed is given and torch is importable."""
    if seed is None:
        return
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(seed)
    except ImportError:
        pass
