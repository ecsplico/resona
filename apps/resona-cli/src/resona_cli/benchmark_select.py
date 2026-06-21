"""Pick the best local engine from a benchmark run and pin it in config.

The standalone harness in ``benchmarks/transcription_benchmark.py`` measures
every installed engine for speed (``x_realtime``) and quality (``wer``) over the
same audio, writing ``benchmarks/results/benchmark_<ts>.json``. This module
reads that JSON and selects a winner with a **lowest-WER-above-a-speed-floor**
rule: an engine must clear the floor in *every* benchmarked language, and among
those the lowest average WER wins.

The selected engine name is written to ``~/.resona/config.json`` as
``default_engine`` (honoured by ``resona transcribe``/``watch``/``live``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Candidate:
    """One engine's aggregated benchmark scores."""

    backend: str
    avg_wer: float
    min_x_realtime: float
    languages: list[str] = field(default_factory=list)
    clears_floor: bool = True
    reason: str = ""


def latest_results_file(results: Path) -> Optional[Path]:
    """Resolve ``results`` to a concrete ``benchmark_*.json`` file.

    Accepts a file path (returned as-is) or a directory (newest matching file
    by name, which is timestamp-sortable). Returns None if nothing is found.
    """
    if results.is_file():
        return results
    if results.is_dir():
        files = sorted(results.glob("benchmark_*.json"))
        return files[-1] if files else None
    return None


def rank_candidates(
    benchmark_results: list[dict],
    *,
    speed_floor: float,
    installed: Optional[set[str]] = None,
) -> list[Candidate]:
    """Rank engines by the lowest-WER-above-speed-floor rule.

    Returns every viable engine as a :class:`Candidate`, those clearing the
    floor first (sorted by ascending avg WER), then the rest (annotated with
    why they were excluded). Cloud/non-installed backends are dropped because
    ``default_engine`` pins a local engine name.
    """
    by_backend: dict[str, list[dict]] = {}
    for row in benchmark_results:
        if "skipped" in row or "error" in row:
            continue
        if "wer" not in row or "x_realtime" not in row:
            continue
        if installed is not None and row["backend"] not in installed:
            continue
        by_backend.setdefault(row["backend"], []).append(row)

    candidates: list[Candidate] = []
    for backend, rows in by_backend.items():
        wers = [r["wer"] for r in rows]
        xrts = [r["x_realtime"] for r in rows]
        avg_wer = sum(wers) / len(wers)
        min_xrt = min(xrts)
        clears = min_xrt >= speed_floor
        reason = "" if clears else (
            f"slowest run {min_xrt:.2f}× < floor {speed_floor:.2f}×"
        )
        candidates.append(Candidate(
            backend=backend,
            avg_wer=avg_wer,
            min_x_realtime=min_xrt,
            languages=sorted({r.get("language", "?") for r in rows}),
            clears_floor=clears,
            reason=reason,
        ))

    candidates.sort(key=lambda c: (not c.clears_floor, c.avg_wer))
    return candidates


def select_best(
    benchmark_results: list[dict],
    *,
    speed_floor: float,
    installed: Optional[set[str]] = None,
) -> tuple[Optional[Candidate], list[Candidate]]:
    """Return ``(winner, ranking)``. ``winner`` is None if none clears the floor."""
    ranking = rank_candidates(
        benchmark_results, speed_floor=speed_floor, installed=installed
    )
    winner = next((c for c in ranking if c.clears_floor), None)
    return winner, ranking


def run_benchmark(
    script: Path,
    *,
    backends: str = "all",
    target_seconds: float = 600.0,
) -> int:
    """Run the standalone benchmark via ``uv run`` with its extra deps.

    Returns the subprocess exit code. The harness writes results to its own
    ``benchmarks/results`` dir, which the caller then reads.
    """
    cmd = [
        "uv", "run",
        "--with", "jiwer", "--with", "datasets", "--with", "soundfile",
        "python", str(script),
        "--backends", backends,
        "--target-seconds", str(target_seconds),
    ]
    return subprocess.call(cmd)


def find_benchmark_script(start: Optional[Path] = None) -> Optional[Path]:
    """Locate ``benchmarks/transcription_benchmark.py`` from cwd upward."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        cand = d / "benchmarks" / "transcription_benchmark.py"
        if cand.is_file():
            return cand
    return None
