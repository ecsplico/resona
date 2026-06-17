#!/usr/bin/env python
"""Resona transcription backend benchmark.

Runs every available ASR backend over the same ~10-minute German and English
audio and writes a log with hardware, model, speed (real-time factor) and
accuracy (word/character error rate).

Backends that aren't installed are skipped (with a logged reason), so this runs
as-is after `uv sync --all-packages` or with a subset of engine extras.

Usage (from the repo root):

    uv run --with jiwer --with datasets --with soundfile \
        python benchmarks/transcription_benchmark.py

    # only some backends, custom duration:
    uv run ... python benchmarks/transcription_benchmark.py \
        --backends faster-whisper,mlx-whisper,whisper-cpp --target-seconds 600

    # bring your own audio + reference (single language):
    uv run ... python benchmarks/transcription_benchmark.py \
        --languages de --audio my.wav --reference my.txt

Each backend uses a large-v3 model (same size) for a fair comparison.
"""

import argparse
import contextlib
import json
import logging
import platform
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _datasets import custom_sample, load_sample  # noqa: E402
from _hardware import hardware_info, relevant_env  # noqa: E402
from _metrics import score  # noqa: E402

log = logging.getLogger("benchmark")

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = HERE / "cache"
DEFAULT_RESULTS = HERE / "results"


LOAD_TIMEOUT_SECONDS = 1200  # guard against a model download/init hang (e.g. HF xet stalls)


@contextlib.contextmanager
def _time_limit(seconds: int, what: str):
    """SIGALRM-based timeout so a hung model load can't block the whole run.

    Main-thread only (the benchmark is single-threaded). On non-Unix or when
    SIGALRM is unavailable it's a no-op.
    """
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise(signum, frame):
        raise TimeoutError(f"{what} exceeded {seconds}s")

    prev = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev)


def _detect_torch_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# --- backend registry -------------------------------------------------------
# Each entry: key, label, default model (large-v3 family), and a factory that
# imports + constructs the transcriber (raising if the engine isn't installed).

def _make_faster_whisper(model):
    from resona_engine_faster_whisper.transcriber import FastWhisperTranscriber

    return FastWhisperTranscriber(device="cpu", modelname=model)


def _make_whisper(model):
    from resona_engine_whisper.transcriber import WhisperTranscriber

    return WhisperTranscriber(device=_detect_torch_device(), modelname=model)


def _make_voxtral(model):
    from resona_engine_voxtral.transcriber import VoxtralTranscriber

    return VoxtralTranscriber(device=_detect_torch_device(), modelname=model)


def _make_mlx(model):
    from resona_engine_mlx_whisper.transcriber import MLXWhisperTranscriber

    return MLXWhisperTranscriber(modelname=model)


def _make_whispercpp(model):
    from resona_engine_whispercpp.transcriber import WhisperCppTranscriber

    return WhisperCppTranscriber(modelname=model)


def _make_lightning(model):
    from resona_engine_lightning_mlx.transcriber import LightningMLXTranscriber

    return LightningMLXTranscriber(modelname=model)


BACKENDS = [
    {"key": "faster-whisper", "label": "faster-whisper (CTranslate2, CPU)",
     "model": "large-v3", "make": _make_faster_whisper},
    {"key": "mlx-whisper", "label": "mlx-whisper (MLX GPU)",
     "model": "mlx-community/whisper-large-v3-mlx", "make": _make_mlx},
    {"key": "whisper-cpp", "label": "whisper.cpp (Metal)",
     "model": "large-v3", "make": _make_whispercpp},
    {"key": "lightning-mlx", "label": "lightning-whisper-mlx (batched MLX GPU)",
     "model": "large-v3", "make": _make_lightning},
    {"key": "whisper", "label": "openai-whisper (PyTorch)",
     "model": "large-v3", "make": _make_whisper},
    {"key": "voxtral", "label": "transformers pipeline",
     "model": "openai/whisper-large-v3", "make": _make_voxtral},
]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--languages", default="de,en", help="comma-separated: de,en")
    p.add_argument("--target-seconds", type=float, default=600.0, help="approx audio length per language")
    p.add_argument("--backends", default="all", help="comma-separated keys or 'all'")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--audio", type=Path, default=None, help="custom audio file (single language)")
    p.add_argument("--reference", type=Path, default=None, help="custom reference transcript")
    p.add_argument("--no-warmup", action="store_true", help="skip the warmup pass (counts model load/compile in timing)")
    return p.parse_args()


def selected_backends(spec: str):
    if spec == "all":
        return BACKENDS
    keys = {k.strip() for k in spec.split(",") if k.strip()}
    chosen = [b for b in BACKENDS if b["key"] in keys]
    unknown = keys - {b["key"] for b in BACKENDS}
    if unknown:
        log.warning("Unknown backend keys ignored: %s", ", ".join(sorted(unknown)))
    return chosen


def load_samples(args):
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]
    samples = {}
    if args.audio:
        if not args.reference:
            sys.exit("--audio requires --reference")
        if len(languages) != 1:
            sys.exit("--audio expects exactly one --languages value")
        audio, ref, dur = custom_sample(str(args.audio), str(args.reference))
        samples[languages[0]] = (audio, ref, dur)
    else:
        for lang in languages:
            audio, ref, dur = load_sample(lang, args.target_seconds, args.cache_dir)
            samples[lang] = (audio, ref, dur)
            log.info("Sample[%s]: %.1fs audio, %d reference words", lang, dur, len(ref.split()))
    return samples


def run_backend(backend, samples, warmup) -> list[dict]:
    """Instantiate a backend once and transcribe every language sample."""
    rows = []
    try:
        log.info("Loading backend: %s (model=%s)", backend["key"], backend["model"])
        t0 = time.perf_counter()
        with _time_limit(LOAD_TIMEOUT_SECONDS, f"{backend['key']} load"):
            engine = backend["make"](backend["model"])
        load_s = time.perf_counter() - t0
    except Exception as e:  # not installed / failed to load
        # Keep the skip reason short — exception chains (e.g. HF download/transformers
        # stacks) can be hundreds of lines and would swamp the markdown log.
        reason = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
        log.warning("Skipping %s: %s", backend["key"], reason)
        return [{"backend": backend["key"], "model": backend["model"], "skipped": reason}]

    if warmup:
        # Warm caches / trigger lazy model load & MLX graph compile on a short clip
        # so it doesn't pollute the timed run.
        first_audio = next(iter(samples.values()))[0]
        try:
            engine.transcribe(first_audio[: 5 * 16000], language=next(iter(samples)))
        except Exception as e:
            log.warning("%s warmup failed (continuing): %s", backend["key"], e)

    for lang, (audio, reference, duration) in samples.items():
        try:
            t0 = time.perf_counter()
            result = engine.transcribe(audio, language=lang)
            elapsed = time.perf_counter() - t0
            metrics = score(reference, result.get("text", ""))
            row = {
                "backend": backend["key"],
                "label": backend["label"],
                "model": backend["model"],
                "language": lang,
                "audio_seconds": round(duration, 1),
                "transcribe_seconds": round(elapsed, 2),
                "rtf": round(elapsed / duration, 4),
                "x_realtime": round(duration / elapsed, 2),
                "wer": round(metrics["wer"], 4),
                "cer": round(metrics["cer"], 4),
                "ref_words": metrics["ref_words"],
                "load_seconds": round(load_s, 2),
            }
            log.info("  %s [%s]: %.1fx realtime, WER=%.3f", backend["key"], lang, row["x_realtime"], row["wer"])
            rows.append(row)
        except Exception as e:
            log.warning("  %s [%s] failed: %s", backend["key"], lang, e)
            rows.append({"backend": backend["key"], "model": backend["model"],
                         "language": lang, "error": str(e)})
    return rows


def write_log(results, samples, args, output_dir: Path, stamp: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    hw = hardware_info()
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "hardware": hw,
        "env": relevant_env(),
        "samples": {lang: {"audio_seconds": round(d, 1), "ref_words": len(r.split())}
                    for lang, (a, r, d) in samples.items()},
        "results": results,
    }
    (output_dir / f"benchmark_{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md = _render_markdown(payload)
    md_path = output_dir / f"benchmark_{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    return md_path


def _render_markdown(payload: dict) -> str:
    hw = payload["hardware"]
    lines = ["# Resona transcription benchmark", "", f"_{payload['timestamp_utc']}_", ""]
    lines += ["## Hardware / environment", ""]
    for k, v in hw.items():
        if v is not None:
            lines.append(f"- **{k}**: {v}")
    if payload["env"]:
        lines += ["", "Tuning env vars:"]
        for k, v in payload["env"].items():
            lines.append(f"- `{k}={v}`")
    lines += ["", "## Samples", ""]
    for lang, s in payload["samples"].items():
        lines.append(f"- **{lang}**: {s['audio_seconds']}s audio, {s['ref_words']} reference words")

    by_lang: dict[str, list[dict]] = {}
    skipped = []
    for r in payload["results"]:
        if "skipped" in r:
            skipped.append(r)
            continue
        by_lang.setdefault(r.get("language", "?"), []).append(r)

    for lang, rows in by_lang.items():
        ok = [r for r in rows if "error" not in r]
        ok.sort(key=lambda r: r["x_realtime"], reverse=True)
        lines += ["", f"## Results — {lang}", "",
                  "| Backend | Model | Audio (s) | Time (s) | RTF | × realtime | WER | CER |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"]
        for r in ok:
            lines.append(
                f"| {r['backend']} | {r['model']} | {r['audio_seconds']} | "
                f"{r['transcribe_seconds']} | {r['rtf']} | {r['x_realtime']} | "
                f"{r['wer']} | {r['cer']} |"
            )
        for r in [r for r in rows if "error" in r]:
            lines.append(f"| {r['backend']} | {r['model']} | — | — | — | — | error: {r['error']} | |")

    if skipped:
        lines += ["", "## Skipped backends (not installed / failed to load)", ""]
        for r in skipped:
            lines.append(f"- **{r['backend']}**: {r['skipped']}")
    lines += ["", "_RTF = transcribe_time / audio_duration (lower is faster). "
              "× realtime = audio_duration / transcribe_time (higher is faster). "
              "WER/CER computed after lowercase + punctuation-stripping normalization._", ""]
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    log.info("Python %s on %s", platform.python_version(), platform.platform())

    samples = load_samples(args)
    backends = selected_backends(args.backends)
    if not backends:
        sys.exit("No backends selected.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results = []
    try:
        for backend in backends:
            results.extend(run_backend(backend, samples, warmup=not args.no_warmup))
            # Rewrite after every backend so a later hang/crash never loses results.
            write_log(results, samples, args, args.output_dir, stamp)
    finally:
        if results:
            md_path = write_log(results, samples, args, args.output_dir, stamp)
            log.info("Wrote benchmark log: %s", md_path)
            print(f"\n{md_path}\n")
            print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
