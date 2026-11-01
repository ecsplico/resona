"""Resona quality control: corpus, scoring, gate and baselines (no ML dependencies)."""

from .baselines import BaselineFileError, comparable, find_baseline, promoted_entry, upsert_baseline
from .corpus import Corpus, CorpusError, Fixture, load_corpus
from .evaluation import (
    EvaluationError,
    FixtureRun,
    build_report,
    detect_platform,
    evaluation_record,
)
from .gate import BASELINE_REQUIRED, TOLERANCE, Gate, evaluate_gate
from .scoring import ScoringError, score_fixture

__all__ = [
    "BASELINE_REQUIRED",
    "TOLERANCE",
    "BaselineFileError",
    "Corpus",
    "CorpusError",
    "EvaluationError",
    "Fixture",
    "FixtureRun",
    "Gate",
    "ScoringError",
    "build_report",
    "comparable",
    "detect_platform",
    "evaluate_gate",
    "evaluation_record",
    "find_baseline",
    "load_corpus",
    "promoted_entry",
    "score_fixture",
    "upsert_baseline",
]
