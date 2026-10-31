"""Per-job execution subprocess.

The supervisor starts one of these per claimed job with the immutable
``execution_snapshot`` and the downloaded input. It runs exactly the candidate
the hub approved, writes a validated ``JobResult`` JSON to ``--out`` and exits
with a code the supervisor maps to a bounded failure code. It never reads
credentials, never talks to the hub and never logs content.

Engines are loaded through the ``resona.engines`` entry points directly, not
via the asr-core registry singleton, which ignores later engine arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping
from enum import IntEnum
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from resona_job_core import Candidate, JobResult, Provenance, Segment, StageProvenance


class ExitCode(IntEnum):
    OK = 0
    UNSUPPORTED_INPUT = 2
    ENGINE_ERROR = 3
    UNSUPPORTED_CAPABILITY = 4
    OUT_OF_MEMORY = 5
    INVALID_SNAPSHOT = 6


class ExecutionFailure(Exception):
    def __init__(self, code: ExitCode, detail: str = "") -> None:
        super().__init__(detail or code.name.lower())
        self.code = code


_PRIVATE_STRIP = (
    "OPENAI_API_KEY",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
)
_OFFLINE_VARS = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}
_DEVICE = {"metal": "mps", "cuda": "cuda", "cpu": "cpu"}


def subprocess_env(*, private: bool, base: Mapping[str, str]) -> dict[str, str]:
    """Environment for the execution subprocess.

    Private jobs never see cloud provider keys and run with HF/telemetry offline.
    """
    env = dict(base)
    if private:
        for name in list(env):
            if name in _PRIVATE_STRIP or name.endswith("_API_KEY"):
                del env[name]
        env.update(_OFFLINE_VARS)
    return env


def _load_snapshot(path: Path) -> tuple[dict[str, Any], Candidate]:
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        candidate = Candidate.model_validate(snapshot["candidate"])
    except (OSError, ValueError, KeyError, ValidationError):
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "invalid snapshot") from None
    if not isinstance(snapshot.get("kind"), str):
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "snapshot has no kind")
    return snapshot, candidate


def _load_engine(name: str) -> type:
    for ep in entry_points(group="resona.engines"):
        if ep.name == name:
            try:
                return ep.load()
            except ImportError:
                break
    raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "engine not available on this worker")


def _segment(seg: Any) -> Segment:
    """Normalize one engine segment: dicts pass through, objects expose attributes."""
    if isinstance(seg, Mapping):
        start, end, text = seg.get("start", 0.0), seg.get("end", 0.0), seg.get("text", "")
    else:
        start, end, text = getattr(seg, "start", 0.0), getattr(seg, "end", 0.0), getattr(seg, "text", "")
    return Segment(start=float(start), end=float(end), text=str(text).strip())


def _run_audio(snapshot: dict[str, Any], candidate: Candidate, input_path: Path) -> JobResult:
    from resona_asr_core.audio import load_audio_path
    from resona_asr_core.model_cache import configure_model_cache

    options = snapshot.get("options") or {}
    if not isinstance(options, dict):
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "options must be an object")

    engine_cls = _load_engine(candidate.engine)
    configure_model_cache()
    try:
        audio = load_audio_path(input_path)
    except (RuntimeError, ValueError, OSError):
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "unreadable audio") from None
    if audio is None or len(audio) == 0:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "empty audio")

    try:
        transcriber = engine_cls(device=_DEVICE[candidate.accelerator], modelname=candidate.model)
    except ImportError:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "engine dependencies missing") from None

    started = time.perf_counter()
    try:
        out = transcriber.transcribe(
            audio,
            language=snapshot.get("language") or "de",
            task="translate" if options.get("translate") else "transcribe",
            word_timestamps=bool(options.get("word_timestamps", False)),
            vad_filter=bool(options.get("vad_filter", False)),
        )
    except MemoryError:
        raise
    except Exception:
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "asr failed") from None
    seconds = time.perf_counter() - started

    text = str(out.get("text", "")).strip()
    segments = [_segment(seg) for seg in out.get("segments", []) or []]
    return JobResult(
        kind="audio",
        raw_text=text,
        markdown=text,
        language=out.get("language") or snapshot.get("language"),
        segments=segments,
        provenance=Provenance(
            stages=[
                StageProvenance(
                    stage="asr",
                    engine=candidate.engine,
                    model=candidate.model,
                    model_revision=candidate.model_revision,
                    accelerator=candidate.accelerator,
                    seconds=seconds,
                )
            ],
        ),
    )


def _run_document(snapshot: dict[str, Any], candidate: Candidate, input_path: Path) -> JobResult:
    try:
        from resona_engine_docling import (
            DocumentOptions,
            UnsupportedDocumentOptions,
            UnsupportedMediaType,
            convert_document,
        )
        from resona_engine_docling.convert import DocumentConversionFailed
    except ImportError:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "docling not installed") from None

    options = dict(snapshot.get("options") or {})
    if "languages" not in options and snapshot.get("language") is not None:
        language = snapshot["language"]
        if language not in ("de", "en"):
            raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "document language unsupported")
        options["languages"] = [language]
    if candidate.ocr_engine is not None:
        requested = options.get("ocr_engine")
        if requested not in (None, candidate.ocr_engine):
            raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "ocr engine not approved for this worker")
        options["ocr_engine"] = candidate.ocr_engine
    try:
        document_options = DocumentOptions.model_validate(options)
    except ValidationError:
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "invalid document options") from None

    try:
        return convert_document(input_path, options=document_options, accelerator=candidate.accelerator)
    except (UnsupportedMediaType, DocumentConversionFailed):
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "document not convertible") from None
    except UnsupportedDocumentOptions:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "document options unsupported") from None
    except MemoryError:
        raise
    except Exception:
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "document conversion failed") from None


def run(snapshot_path: Path, input_path: Path, out_path: Path) -> ExitCode:
    snapshot, candidate = _load_snapshot(snapshot_path)
    kind = snapshot["kind"]
    if kind == "audio":
        result = _run_audio(snapshot, candidate, input_path)
    elif kind == "document":
        result = _run_document(snapshot, candidate, input_path)
    else:
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "unknown kind")
    out_path.write_text(result.model_dump_json(), encoding="utf-8")
    return ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resona-worker-exec")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return int(run(args.snapshot, args.input, args.out))
    except ExecutionFailure as exc:
        return int(exc.code)
    except MemoryError:
        return int(ExitCode.OUT_OF_MEMORY)


if __name__ == "__main__":
    sys.exit(main())
