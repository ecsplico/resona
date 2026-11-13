"""Per-job execution subprocess.

The supervisor starts one of these per claimed job with the immutable
``execution_snapshot`` and the downloaded input. It runs exactly the candidate
the hub approved, writes a validated ``JobResult`` JSON to ``--out`` and exits
with a code the supervisor maps to a bounded failure code. It never reads
credentials, never talks to the hub and never logs content.

Engines are loaded through the ``resona.engines`` entry points directly, not
via the asr-core registry singleton, which ignores later engine arguments.
HuggingFace-hosted weights are loaded from the exact local snapshot prepared
for the candidate; nothing is downloaded while serving a job.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import re
import sys
import time
from collections.abc import Mapping
from enum import IntEnum
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError
from resona_job_core import Candidate, JobResult, Provenance, Segment, StageProvenance
from resona_job_core.features import known_features, required_features
from resona_job_core.options import Language

from .weights import (
    WeightsError,
    is_hub_repo,
    is_whisper_alias,
    local_snapshot,
    release_model_pins,
    whisper_checkpoint,
    whisper_revision,
)


class ExitCode(IntEnum):
    OK = 0
    UNSUPPORTED_INPUT = 2
    ENGINE_ERROR = 3
    UNSUPPORTED_CAPABILITY = 4
    OUT_OF_MEMORY = 5
    INVALID_SNAPSHOT = 6
    # The diarizer stage failed (not installed, weights not prepared, out of memory).
    # The supervisor stops offering diarization but keeps the ASR tuple.
    DIARIZER_UNAVAILABLE = 7
    DIARIZER_OUT_OF_MEMORY = 8
    # The snapshot sets an option this release does not know (a hub predating the
    # claim-time feature check handed it out). Retryable: a newer worker can run it.
    UNKNOWN_OPTIONS = 9


# Written to stderr before the diarizer loads, so the supervisor can tell a native
# out-of-memory abort (SIGKILL, ``[metal::malloc]``) in that stage from one in ASR.
DIARIZE_STAGE_MARKER = "resona-stage: diarize"


class ExecutionFailure(Exception):
    def __init__(self, code: ExitCode, detail: str = "") -> None:
        super().__init__(detail or code.name.lower())
        self.code = code


# Private jobs see only these variables (plus the forced offline switches below).
_PRIVATE_ALLOW = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "TZ",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
        "LD_LIBRARY_PATH",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "XDG_CACHE_HOME",
        "TORCH_HOME",
        "RESONA_MODELS_DIR",
        "VOICEBOX_MODELS_DIR",
        "LOGLEVEL",
    }
)
_PRIVATE_ALLOW_PREFIXES = (
    "LC_",
    "FASTWHISPER_",
    "WHISPERCPP_",
    "LIGHTNING_MLX_",
    "RESONA_PARAKEET_",
    "MLX_",
    "PYTORCH_",
    "OMP_",
    "MKL_",
    "CUDA_",
    "NVIDIA_",
)
# Credential-shaped names are dropped even when an allowed prefix matches them.
_CREDENTIAL_NAME = re.compile(r"(^|_)(API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)($|_)")
_OFFLINE_VARS = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
    "ANONYMIZED_TELEMETRY": "False",
}
_DEVICE = {"metal": "mps", "cuda": "cuda", "cpu": "cpu"}

# Lower-cased fragments that identify an out-of-memory failure: Python, MLX's Metal
# allocator, PyTorch CUDA/MPS (``OutOfMemoryError``, "... out of memory") and Metal
# command-buffer OOM (``kIOGPUCommandBufferCallbackErrorOutOfMemory``).
OOM_MARKERS = ("memoryerror", "[metal::malloc]", "out of memory", "outofmemory")

# A pause of at least this long between two segments starts a new paragraph.
PARAGRAPH_PAUSE_SECONDS = 1.5
# A paragraph this long ends after the next segment that ends a sentence; at twice this
# length (engines without punctuation) it ends at the next segment.
PARAGRAPH_MAX_CHARS = 1000
_SENTENCE_END = re.compile(r"[.!?…][\"'»«“”’)\]]*$")


def mentions_out_of_memory(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in OOM_MARKERS)


def _is_out_of_memory(exc: BaseException) -> bool:
    return isinstance(exc, MemoryError) or mentions_out_of_memory(f"{type(exc).__name__}: {exc}")


def subprocess_env(*, private: bool, base: Mapping[str, str]) -> dict[str, str]:
    """Environment for the execution subprocess.

    Private jobs get an allowlisted environment: system/locale basics, model-cache
    locations and engine tuning knobs. Cloud credentials and anything credential-shaped
    never pass; HuggingFace runs offline and telemetry is disabled. Non-private jobs
    inherit the worker environment (cloud engines need their provider keys) minus the
    hub client key, which no engine needs.
    """
    if not private:
        return {name: value for name, value in base.items() if name != "RESONA_API_KEY"}
    env = {
        name: value
        for name, value in base.items()
        if (name in _PRIVATE_ALLOW or name.startswith(_PRIVATE_ALLOW_PREFIXES))
        and not _CREDENTIAL_NAME.search(name)
    }
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


def _resolve_weights(candidate: Candidate) -> tuple[str, str | None]:
    """Return (model argument for the engine, resolved model revision).

    HuggingFace repos resolve to the local snapshot at the pinned revision (or the
    cached ``main``); the snapshot directory name is the commit hash. openai-whisper
    aliases resolve to the prepared checkpoint file (passing the path keeps the
    library from downloading); their revision is the published SHA-256. Missing
    weights mean this worker was not prepared for the candidate.
    """
    if is_whisper_alias(candidate.engine, candidate.model):
        revision = whisper_revision(candidate.model)
        if candidate.model_revision not in (None, revision):
            raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "installed whisper does not match the pinned revision")
        checkpoint = whisper_checkpoint(candidate.model)
        if checkpoint is None:
            raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "model weights not prepared on this worker")
        return str(checkpoint), revision
    if not is_hub_repo(candidate.model):
        return candidate.model, candidate.model_revision
    snapshot = local_snapshot(candidate.model, candidate.model_revision)
    if snapshot is None:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "model weights not prepared on this worker")
    return str(snapshot), snapshot.name


def accelerator_available(accelerator: str) -> bool:
    """True when this host can run ``accelerator`` now (cuda: PyTorch sees a CUDA device)."""
    if accelerator == "cuda":
        try:
            import torch
        except ImportError:
            return False
        try:
            return bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - a broken driver means "unavailable"
            return False
    if accelerator == "metal":
        return sys.platform == "darwin" and platform.machine() == "arm64"
    return accelerator == "cpu"


def _require_accelerator(candidate: Candidate) -> None:
    # Frameworks silently fall back to CPU (Docling) or fail late (PyTorch); provenance must
    # never claim a device that did not run, so a missing device is a missing capability.
    if not accelerator_available(candidate.accelerator):
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, f"{candidate.accelerator} not available on this host")


def _segment(seg: Any) -> Segment:
    """Normalize one engine segment: dicts pass through, objects expose attributes."""
    if isinstance(seg, Mapping):
        start, end, text = seg.get("start", 0.0), seg.get("end", 0.0), seg.get("text", "")
    else:
        start, end, text = getattr(seg, "start", 0.0), getattr(seg, "end", 0.0), getattr(seg, "text", "")
    return Segment(start=float(start), end=float(end), text=str(text).strip())


def audio_markdown(segments: list[Segment], text: str) -> str:
    """Markdown for an audio transcript: segments joined with spaces, paragraphs blank-line separated.

    A paragraph ends at a pause of ``PARAGRAPH_PAUSE_SECONDS`` or more, or once it is
    ``PARAGRAPH_MAX_CHARS`` long, after the next segment ending a sentence (at twice that
    length, at the next segment). Without segment text (engines that return none) the
    plain transcript is the Markdown.
    """
    paragraphs: list[list[str]] = []
    paragraph_chars = 0
    previous_end = 0.0
    for seg in segments:
        if not seg.text:
            continue
        if paragraphs:
            open_paragraph = paragraphs[-1]
            pause = seg.start - previous_end >= PARAGRAPH_PAUSE_SECONDS
            sentence_done = _SENTENCE_END.search(open_paragraph[-1]) is not None
            too_long = paragraph_chars >= 2 * PARAGRAPH_MAX_CHARS or (
                paragraph_chars >= PARAGRAPH_MAX_CHARS and sentence_done
            )
            starts_paragraph = pause or too_long
        else:
            starts_paragraph = True
        if starts_paragraph:
            paragraphs.append([seg.text])
            paragraph_chars = len(seg.text)
        else:
            paragraphs[-1].append(seg.text)
            paragraph_chars += len(" ") + len(seg.text)
        previous_end = seg.end
    if not paragraphs:
        return text
    return "\n\n".join(" ".join(paragraph) for paragraph in paragraphs)


# Speaker label of a diarized Markdown section, by transcript language.
_SPEAKER_LABEL = {"de": "Sprecher"}
_DEFAULT_SPEAKER_LABEL = "Speaker"


def _engine_words(seg: Any) -> list[Any]:
    """The word timestamps of one engine segment (dicts or objects, Whisper-style ``word``)."""
    from resona_diarize import Word

    raw = seg.get("words") if isinstance(seg, Mapping) else getattr(seg, "words", None)
    words = []
    for item in raw or []:
        if isinstance(item, Mapping):
            start, end, text = item.get("start"), item.get("end"), item.get("word", item.get("text", ""))
        else:
            start, end = getattr(item, "start", None), getattr(item, "end", None)
            text = getattr(item, "word", getattr(item, "text", ""))
        if start is None or end is None:
            continue
        words.append(Word(float(start), float(end), str(text)))
    return words


def diarized_transcript(
    raw_segments: list[Any], text: str, duration: float, turns: list[Any], language: str | None
) -> tuple[list[Segment], str] | None:
    """Speaker sections and their Markdown, or ``None`` when the diarizer found no speech.

    Every word gets the speaker whose turns cover most of it; consecutive words of one
    speaker form a section, which becomes one ``Segment`` with ``speaker``. A segment
    without word timestamps counts as one word; an engine without segments, as one word
    spanning the recording. In Markdown each section starts with ``**Sprecher N:** ``
    (``Speaker`` for other languages) and is broken into paragraphs like
    :func:`audio_markdown`, over the ASR segments inside it.
    """
    from resona_diarize import Word, assign_words, sections

    words: list[Word] = []
    owners: list[int] = []
    for index, seg in enumerate(raw_segments):
        found = _engine_words(seg)
        if not found:
            plain = _segment(seg)
            found = [Word(plain.start, plain.end, f" {plain.text}")] if plain.text else []
        words.extend(found)
        owners.extend([index] * len(found))
    if not words and text:
        words, owners = [Word(0.0, duration, f" {text}")], [0]
    groups = sections(words, assign_words(words, turns))
    if not groups:
        return None

    label = _SPEAKER_LABEL.get(language or "", _DEFAULT_SPEAKER_LABEL)
    segments: list[Segment] = []
    blocks: list[str] = []
    position = 0
    for group in groups:
        group_owners = owners[position : position + len(group.words)]
        position += len(group.words)
        # One piece per ASR segment inside the section, for paragraph breaks at pauses.
        pieces: list[Segment] = []
        run: list[Any] = []
        for word, owner, next_owner in zip(group.words, group_owners, [*group_owners[1:], None], strict=True):
            run.append(word)
            if next_owner != owner:
                piece = "".join(w.text for w in run).strip()
                pieces.append(Segment(start=run[0].start, end=run[-1].end, text=piece))
                run = []
        segments.append(Segment(start=group.start, end=group.end, text=group.text, speaker=group.speaker))
        blocks.append(f"**{label} {group.speaker}:** {audio_markdown(pieces, group.text)}")
    return segments, "\n\n".join(blocks)


def _diarizer_weights() -> tuple[str, str, Path]:
    """(engine, model, pinned local snapshot) of the release's diarizer; missing = not prepared."""
    try:
        from resona_diarize import ENGINE, MODEL, WEIGHT_PATTERNS
    except ImportError:
        raise ExecutionFailure(ExitCode.DIARIZER_UNAVAILABLE, "diarizer not installed") from None
    try:
        pin = release_model_pins().get(MODEL)
    except WeightsError:
        pin = None
    snapshot = local_snapshot(MODEL, pin, WEIGHT_PATTERNS) if pin else None
    if snapshot is None:
        raise ExecutionFailure(ExitCode.DIARIZER_UNAVAILABLE, "diarizer weights not prepared on this worker")
    return ENGINE, MODEL, snapshot


def _diarize(audio: Any, candidate: Candidate, snapshot: Path) -> tuple[list[Any], float]:
    """Speaker turns of the recording on the candidate's accelerator, and the seconds it took."""
    print(DIARIZE_STAGE_MARKER, file=sys.stderr, flush=True)
    try:
        from resona_diarize.model import Diarizer

        diarizer = Diarizer(str(snapshot), _DEVICE[candidate.accelerator])
    except ImportError:
        raise ExecutionFailure(ExitCode.DIARIZER_UNAVAILABLE, "diarizer dependencies missing") from None
    except Exception as exc:
        if _is_out_of_memory(exc):
            raise ExecutionFailure(ExitCode.DIARIZER_OUT_OF_MEMORY) from None
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "diarizer load failed") from None
    started = time.perf_counter()
    try:
        turns = diarizer(audio)
    except Exception as exc:
        if _is_out_of_memory(exc):
            raise ExecutionFailure(ExitCode.DIARIZER_OUT_OF_MEMORY) from None
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "diarization failed") from None
    return turns, time.perf_counter() - started


def _run_audio(snapshot: dict[str, Any], candidate: Candidate, input_path: Path) -> JobResult:
    from resona_asr_core.audio import SAMPLE_RATE, load_audio_path
    from resona_asr_core.model_cache import configure_model_cache

    options = snapshot.get("options") or {}
    if not isinstance(options, dict):
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "options must be an object")
    diarize = bool(options.get("diarize", False))

    engine_cls = _load_engine(candidate.engine)
    configure_model_cache()
    model_arg, revision = _resolve_weights(candidate)
    diarizer_weights = _diarizer_weights() if diarize else None
    _require_accelerator(candidate)
    try:
        audio = load_audio_path(input_path)
    except (RuntimeError, ValueError, OSError):
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "unreadable audio") from None
    if audio is None or len(audio) == 0:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "empty audio")

    try:
        transcriber = engine_cls(device=_DEVICE[candidate.accelerator], modelname=model_arg)
    except ImportError:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "engine dependencies missing") from None
    except Exception as exc:
        if _is_out_of_memory(exc):
            raise ExecutionFailure(ExitCode.OUT_OF_MEMORY) from None
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "asr model load failed") from None

    started = time.perf_counter()
    try:
        out = transcriber.transcribe(
            audio,
            language=snapshot.get("language") or "de",
            task="translate" if options.get("translate") else "transcribe",
            # Speaker assignment is per word: diarized jobs always need word timestamps.
            word_timestamps=diarize or bool(options.get("word_timestamps", False)),
            vad_filter=bool(options.get("vad_filter", False)),
        )
    except Exception as exc:
        if _is_out_of_memory(exc):
            raise ExecutionFailure(ExitCode.OUT_OF_MEMORY) from None
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "asr failed") from None
    seconds = time.perf_counter() - started

    text = str(out.get("text", "")).strip()
    language = out.get("language") or snapshot.get("language")
    raw_segments = list(out.get("segments", []) or [])
    segments = [_segment(seg) for seg in raw_segments]
    markdown = audio_markdown(segments, text)
    stages = [
        StageProvenance(
            stage="asr",
            engine=candidate.engine,
            model=candidate.model,
            model_revision=revision,
            accelerator=candidate.accelerator,
            seconds=seconds,
        )
    ]
    if diarizer_weights is not None:
        engine, model, weights = diarizer_weights
        # One process, one device: free the ASR model before loading the diarizer.
        del transcriber
        gc.collect()
        turns, diarize_seconds = _diarize(audio, candidate, weights)
        # A translated transcript is English, whatever language was spoken.
        label_language = "en" if options.get("translate") else language
        diarized = diarized_transcript(raw_segments, text, len(audio) / SAMPLE_RATE, turns, label_language)
        if diarized is not None:
            segments, markdown = diarized
        stages.append(
            StageProvenance(
                stage="diarize",
                engine=engine,
                model=model,
                model_revision=weights.name,
                accelerator=candidate.accelerator,
                seconds=diarize_seconds,
            )
        )
    return JobResult(
        kind="audio",
        raw_text=text,
        markdown=markdown,
        language=language,
        segments=segments,
        provenance=Provenance(stages=stages),
    )


def _run_document(snapshot: dict[str, Any], candidate: Candidate, input_path: Path) -> JobResult:
    try:
        from resona_engine_docling import (
            DOCX_SUFFIXES,
            ConflictingDocumentOptions,
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
        if language not in get_args(Language):
            raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "document language unsupported")
        options["languages"] = [language]
    # A DOCX runs no OCR, so the candidate's OCR engine is not injected (any docling worker serves it).
    if candidate.ocr_engine is not None and input_path.suffix.lower() not in DOCX_SUFFIXES:
        requested = options.get("ocr_engine")
        if requested not in (None, candidate.ocr_engine):
            raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "ocr engine not approved for this worker")
        options["ocr_engine"] = candidate.ocr_engine
    try:
        document_options = DocumentOptions.model_validate(options)
    except ValidationError:
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "invalid document options") from None

    _require_accelerator(candidate)
    try:
        result = convert_document(input_path, options=document_options, accelerator=candidate.accelerator)
    except (UnsupportedMediaType, DocumentConversionFailed):
        raise ExecutionFailure(ExitCode.UNSUPPORTED_INPUT, "document not convertible") from None
    except ConflictingDocumentOptions:
        # No worker can run these options: fail the job instead of withdrawing this worker's capability.
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "document options conflict") from None
    except UnsupportedDocumentOptions:
        raise ExecutionFailure(ExitCode.UNSUPPORTED_CAPABILITY, "document options unsupported") from None
    except Exception as exc:
        if _is_out_of_memory(exc):
            raise ExecutionFailure(ExitCode.OUT_OF_MEMORY) from None
        raise ExecutionFailure(ExitCode.ENGINE_ERROR, "document conversion failed") from None
    return result.model_copy(update={"provenance": _resolved_stage_revisions(result.provenance)})


def _resolved_stage_revisions(provenance: Provenance) -> Provenance:
    """Replace branch/tag revisions of HuggingFace-hosted stage models by the loaded snapshot hash."""
    stages = []
    for stage in provenance.stages:
        if is_hub_repo(stage.model):
            snapshot = local_snapshot(stage.model, stage.model_revision)
            if snapshot is not None:
                stage = stage.model_copy(update={"model_revision": snapshot.name})
        stages.append(stage)
    return provenance.model_copy(update={"stages": stages})


def peak_accelerator_bytes() -> int | None:
    """Peak device memory of this process where the framework reports a true peak (MLX, CUDA)."""
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        try:
            peak = int(mx.get_peak_memory())
            if peak > 0:  # MLX imported as a dependency but unused reports 0
                return peak
        except Exception:  # noqa: BLE001 - metrics are best effort
            pass
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_initialized():
                return int(torch.cuda.max_memory_allocated())
        except Exception:  # noqa: BLE001 - metrics are best effort
            pass
    return None


def _with_stage_peaks(result: JobResult, peak: int | None) -> JobResult:
    """Record the process's peak device memory on the stages that ran on the device.

    One execution process runs one job, so the peak covers model load and inference.
    Docling interleaves layout and table models per page; both carry the same process peak.
    """
    if peak is None:
        return result
    stages = [
        stage.model_copy(update={"peak_accelerator_bytes": peak}) if stage.accelerator in ("cuda", "metal") else stage
        for stage in result.provenance.stages
    ]
    return result.model_copy(update={"provenance": result.provenance.model_copy(update={"stages": stages})})


def run(snapshot_path: Path, input_path: Path, out_path: Path, metrics_path: Path | None = None) -> ExitCode:
    snapshot, candidate = _load_snapshot(snapshot_path)
    kind = snapshot["kind"]
    options = snapshot.get("options")
    if kind in ("audio", "document") and isinstance(options, dict) and required_features(kind, options) - known_features():
        raise ExecutionFailure(ExitCode.UNKNOWN_OPTIONS, "options unknown to this release")
    if kind == "audio":
        result = _run_audio(snapshot, candidate, input_path)
    elif kind == "document":
        result = _run_document(snapshot, candidate, input_path)
    else:
        raise ExecutionFailure(ExitCode.INVALID_SNAPSHOT, "unknown kind")
    peak = peak_accelerator_bytes()
    result = _with_stage_peaks(result, peak)
    out_path.write_text(result.model_dump_json(), encoding="utf-8")
    if metrics_path is not None:
        metrics_path.write_text(json.dumps({"peak_accelerator_bytes": peak}), encoding="utf-8")
    return ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--probe-accelerator"]:
        # `--probe-accelerator <cpu|cuda|metal>`: exit 0 when usable, else UNSUPPORTED_CAPABILITY.
        usable = len(argv) == 2 and accelerator_available(argv[1])
        return int(ExitCode.OK if usable else ExitCode.UNSUPPORTED_CAPABILITY)
    parser = argparse.ArgumentParser(prog="resona-worker-exec")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        return int(run(args.snapshot, args.input, args.out, args.metrics))
    except ExecutionFailure as exc:
        return int(exc.code)
    except MemoryError:
        return int(ExitCode.OUT_OF_MEMORY)


if __name__ == "__main__":
    sys.exit(main())
