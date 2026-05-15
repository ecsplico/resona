"""Preload pip-installed NVIDIA CUDA libraries so CTranslate2 can find them.

faster-whisper's CTranslate2 backend links against ``libcublas.so.12`` and
``libcudnn.so.9``. When these come from the ``nvidia-cublas-cu12`` /
``nvidia-cudnn-cu12`` pip wheels (rather than a system-wide CUDA install), the
dynamic loader will not find them unless ``LD_LIBRARY_PATH`` was set *before*
the process started. Loading the shared objects with ``RTLD_GLOBAL`` makes them
resolvable for CTranslate2's later ``dlopen`` — this is the same mechanism
PyTorch uses internally for its bundled CUDA libraries, and is why a plain
``import torch`` used to make the GPU "just work" here.
"""

import ctypes
import importlib.util
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# (pip package providing the lib dir, SONAME to load). cuBLAS first: cuDNN's
# kernels depend on cuBLAS symbols, so cuBLAS must be in the process first.
_CUDA_LIBS: list[tuple[str, str]] = [
    ("nvidia.cublas.lib", "libcublas.so.12"),
    ("nvidia.cudnn.lib", "libcudnn.so.9"),
]


def preload_cuda_libs() -> None:
    """Best-effort preload of CUDA libraries from ``nvidia-*`` pip wheels.

    Safe no-op when the packages are absent (CPU-only install) or when the
    libraries are already provided by the system. Never raises.
    """
    for module_name, soname in _CUDA_LIBS:
        spec = importlib.util.find_spec(module_name)
        if spec is None or not spec.submodule_search_locations:
            continue
        lib_dir = Path(next(iter(spec.submodule_search_locations)))
        so_path = lib_dir / soname
        if not so_path.exists():
            continue
        try:
            ctypes.CDLL(str(so_path), mode=ctypes.RTLD_GLOBAL)
            log.debug("Preloaded CUDA library %s", so_path)
        except OSError as exc:
            log.warning("Could not preload %s: %s", so_path, exc)
