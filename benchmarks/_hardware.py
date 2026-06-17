"""Collect hardware / environment info for the benchmark log."""

import os
import platform
import subprocess
import sys


def _sysctl(key: str) -> str | None:
    try:
        return subprocess.check_output(
            ["sysctl", "-n", key], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def hardware_info() -> dict:
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
    }
    if sys.platform == "darwin":
        info["chip"] = _sysctl("machdep.cpu.brand_string")
        mem = _sysctl("hw.memsize")
        info["memory_gb"] = round(int(mem) / 1e9, 1) if mem else None
        info["performance_cores"] = _sysctl("hw.perflevel0.physicalcpu")
        info["efficiency_cores"] = _sysctl("hw.perflevel1.physicalcpu")
        info["gpu_cores"] = _sysctl("hw.gpu.core_count")
    return info


def relevant_env() -> dict:
    """Echo the tuning env vars that affect the engines, for reproducibility."""
    keys = [
        "FASTWHISPER_BEAM_SIZE", "FASTWHISPER_CPU_THREADS", "FASTWHISPER_BATCHED",
        "FASTWHISPER_BATCH_SIZE", "FASTWHISPER_COMPUTE_TYPE",
        "DEFAULT_FASTWHISPER_MODEL", "DEFAULT_MLX_WHISPER_MODEL",
        "DEFAULT_WHISPERCPP_MODEL", "WHISPERCPP_N_THREADS",
        "DEFAULT_LIGHTNING_MLX_MODEL", "LIGHTNING_MLX_BATCH_SIZE", "LIGHTNING_MLX_QUANT",
    ]
    return {k: os.environ[k] for k in keys if k in os.environ}
